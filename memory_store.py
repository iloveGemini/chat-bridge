import os
import sys
import json
import time
import array
import math
import sqlite3
import threading
import tempfile
from typing import Any, Optional, Union

# 全局数据库连接与多线程写锁
_conn: Optional[sqlite3.Connection] = None
_lock = threading.Lock()


def _cosine(a: list[float], b: list[float]) -> float:
    """纯 Python 实现的余弦相似度计算，处理零向量与维度不一致"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a)
    norm_b = sum(x * x for x in b)
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(dot / (math.sqrt(norm_a) * math.sqrt(norm_b)))


def _encode_vec(vec: Optional[list[float]]) -> Optional[bytes]:
    """将 float 列表编码为 float32 BLOB"""
    if vec is None:
        return None
    return array.array('f', vec).tobytes()


def _decode_vec(blob: Optional[bytes]) -> Optional[list[float]]:
    """将 float32 BLOB 解码为 float 列表"""
    if blob is None:
        return None
    a = array.array('f')
    a.frombytes(blob)
    return list(a)


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """将 SQLite Row 转化为标准字典，自动处理特殊字段"""
    d = dict(row)
    # 向量不进返回 dict：避免把 1024 维 embedding 塞满 HTTP 响应；算分时按需从原始行解码
    d.pop('embedding', None)
    if 'caused_by' in d and isinstance(d['caused_by'], str):
        try:
            d['caused_by'] = json.loads(d['caused_by'])
        except Exception:
            d['caused_by'] = []
    return d


# ==================== 初始化与管理 ====================

def init_db(db_path: str) -> None:
    """建表 + WAL 模式配置，保证幂等性"""
    global _conn
    with _lock:
        _conn = sqlite3.connect(db_path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        
        # 开启 WAL 模式提高并发读写性能
        _conn.execute("PRAGMA journal_mode=WAL;")
        
        # 1. 核心事件表
        _conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          scope TEXT NOT NULL,
          session_id TEXT,
          msg_start INTEGER, msg_end INTEGER,
          type TEXT,                    -- 相遇/冲突/揭示/抉择/羁绊/转变/收束/日常
          weight TEXT,                  -- 核心/主线/转折/点睛/氛围
          summary TEXT NOT NULL,
          caused_by TEXT DEFAULT '[]',  -- JSON 数组字符串
          embedding BLOB,               -- float32，可空
          importance INTEGER DEFAULT 3,
          created_at TEXT, last_seen_at TEXT, hits INTEGER DEFAULT 0
        );
        """)
        _conn.execute("CREATE INDEX IF NOT EXISTS idx_events_scope ON events(scope);")
        
        # 2. 原始文本切片表
        _conn.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          scope TEXT NOT NULL,
          session_id TEXT,
          msg_floor INTEGER,
          text TEXT NOT NULL,
          embedding BLOB,
          created_at TEXT
        );
        """)
        _conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_scope ON chunks(scope);")
        
        # 3. 三元组事实表
        _conn.execute("""
        CREATE TABLE IF NOT EXISTS facts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          scope TEXT NOT NULL,
          subject TEXT, predicate TEXT, object TEXT,
          updated_at TEXT,
          UNIQUE(scope, subject, predicate)
        );
        """)
        
        # 4. 全局/会话摘要表
        _conn.execute("""
        CREATE TABLE IF NOT EXISTS summaries (
          key TEXT PRIMARY KEY,         -- 例如 "session:default" 或 "arc:char:许今闻"
          text TEXT, updated_at TEXT
        );
        """)

        # 5. 元数据表（总结边界/状态等，data 存 JSON 字符串）
        _conn.execute("""
        CREATE TABLE IF NOT EXISTS meta (
          key TEXT PRIMARY KEY,
          data TEXT, updated_at TEXT
        );
        """)
        _conn.commit()


def close_db() -> None:
    """关闭数据库连接"""
    global _conn
    with _lock:
        if _conn:
            _conn.close()
            _conn = None


# ==================== 写入数据原语 ====================

def upsert_event(scope: str, summary: str, *, session_id: Optional[str] = None, 
                 type: Optional[str] = None, weight: Optional[str] = None,
                 caused_by: Optional[list] = None, embedding: Optional[list[float]] = None, 
                 importance: int = 3, msg_start: Optional[int] = None, 
                 msg_end: Optional[int] = None) -> int:
    """保存或追加重要事件记录，返回自增 ID"""
    global _conn
    if _conn is None:
        raise RuntimeError("Database not initialized. Call init_db first.")
    
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    caused_by_str = json.dumps(caused_by) if caused_by is not None else '[]'
    blob = _encode_vec(embedding)
    
    with _lock:
        cursor = _conn.cursor()
        cursor.execute("""
            INSERT INTO events (
                scope, session_id, msg_start, msg_end, type, weight, 
                summary, caused_by, embedding, importance, created_at, last_seen_at, hits
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """, (scope, session_id, msg_start, msg_end, type, weight, 
              summary, caused_by_str, blob, importance, now, now))
        _conn.commit()
        return cursor.lastrowid


def add_chunk(scope: str, text: str, *, session_id: Optional[str] = None, 
              msg_floor: Optional[int] = None, embedding: Optional[list[float]] = None) -> int:
    """添加一条聊天切片细节"""
    global _conn
    if _conn is None:
        raise RuntimeError("Database not initialized.")
    
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    blob = _encode_vec(embedding)
    
    with _lock:
        cursor = _conn.cursor()
        cursor.execute("""
            INSERT INTO chunks (scope, session_id, msg_floor, text, embedding, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (scope, session_id, msg_floor, text, blob, now))
        _conn.commit()
        return cursor.lastrowid


def upsert_fact(scope: str, subject: str, predicate: str, obj: str) -> None:
    """按 (scope, subject, predicate) 覆盖或写入硬事实"""
    global _conn
    if _conn is None:
        raise RuntimeError("Database not initialized.")
    
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        _conn.execute("""
            INSERT INTO facts (scope, subject, predicate, object, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(scope, subject, predicate) DO UPDATE SET
                object = excluded.object,
                updated_at = excluded.updated_at
        """, (scope, subject, predicate, obj, now))
        _conn.commit()


def upsert_summary(key: str, text: str) -> None:
    """按 key 覆盖全局大纲或会话近况摘要"""
    global _conn
    if _conn is None:
        raise RuntimeError("Database not initialized.")
    
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        _conn.execute("""
            INSERT INTO summaries (key, text, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                text = excluded.text,
                updated_at = excluded.updated_at
        """, (key, text, now))
        _conn.commit()


# ==================== 记忆检索原语 ====================

def recall_events(scope: str, *, query_vec: Optional[list[float]] = None, 
                  query_text: str = "", k: int = 5, recall_n: int = 30) -> list[dict]:
    """高精度/降级检索核心事件"""
    global _conn
    if _conn is None:
        return []
    
    # 场景 A: 基于向量的召回 + 权重调节排序
    if query_vec is not None:
        cursor = _conn.execute("SELECT * FROM events WHERE scope = ? AND embedding IS NOT NULL", (scope,))
        rows = cursor.fetchall()
        
        scored_items = []
        for r in rows:
            d = _row_to_dict(r)
            d['score'] = _cosine(query_vec, _decode_vec(r['embedding']))
            scored_items.append(d)
        
        # 先按原始余弦相似度进行高位截断召回
        scored_items.sort(key=lambda x: x['score'], reverse=True)
        top_recalled = scored_items[:recall_n]
        
        # 配合角色设定权重系数进行二次重排
        weight_map = {'核心': 1.3, '主线': 1.15, '转折': 1.1, '点睛': 1.0, '氛围': 0.9}
        def _final_rank_key(item):
            w = weight_map.get(item.get('weight'), 1.0)
            return item['score'] * w
            
        top_recalled.sort(key=_final_rank_key, reverse=True)
        return top_recalled[:k]
        
    # 场景 B: 降级到文本或纯时间排序
    else:
        cursor = _conn.execute("SELECT * FROM events WHERE scope = ?", (scope,))
        rows = [_row_to_dict(r) for r in cursor.fetchall()]
        words = [w for w in query_text.split() if w]
        
        if words:
            def _calc_match_hit(item):
                summary = item.get('summary', '')
                return 1 if any(w in summary for w in words) else 0
            
            # 排序策略：命中关键词优先 > 重要度降序 > 时间降序
            rows.sort(key=lambda x: (_calc_match_hit(x), x.get('importance', 3), x.get('created_at', '')), reverse=True)
            return rows[:k]
        else:
            # 纯时间线降级
            rows.sort(key=lambda x: x.get('created_at', ''), reverse=True)
            return rows[:k]


def recall_chunks(scope: str, *, query_vec: Optional[list[float]] = None, 
                  query_text: str = "", k: int = 5, recall_n: int = 30) -> list[dict]:
    """细节切片检索层"""
    global _conn
    if _conn is None:
        return []
        
    if query_vec is not None:
        cursor = _conn.execute("SELECT * FROM chunks WHERE scope = ? AND embedding IS NOT NULL", (scope,))
        rows = cursor.fetchall()
        
        scored_items = []
        for r in rows:
            d = _row_to_dict(r)
            d['score'] = _cosine(query_vec, _decode_vec(r['embedding']))
            scored_items.append(d)
            
        scored_items.sort(key=lambda x: x['score'], reverse=True)
        return scored_items[:recall_n][:k]
    else:
        cursor = _conn.execute("SELECT * FROM chunks WHERE scope = ?", (scope,))
        rows = [_row_to_dict(r) for r in cursor.fetchall()]
        words = [w for w in query_text.split() if w]
        
        if words:
            def _calc_match_hit(item):
                text = item.get('text', '')
                return 1 if any(w in text for w in words) else 0
            rows.sort(key=lambda x: (_calc_match_hit(x), x.get('created_at', '')), reverse=True)
            return rows[:k]
        else:
            rows.sort(key=lambda x: x.get('created_at', ''), reverse=True)
            return rows[:k]


def get_facts(scope: str) -> list[dict]:
    """提取该作用域下的所有实体静态硬事实"""
    global _conn
    if _conn is None:
        return []
    cursor = _conn.execute("SELECT * FROM facts WHERE scope = ?", (scope,))
    return [_row_to_dict(r) for r in cursor.fetchall()]


def get_summary(key: str) -> Optional[str]:
    """快速读取指定摘要内容"""
    global _conn
    if _conn is None:
        return None
    cursor = _conn.execute("SELECT text FROM summaries WHERE key = ?", (key,))
    row = cursor.fetchone()
    return row[0] if row else None


# ==================== 元数据（总结边界/状态） ====================

def get_meta(key: str) -> Optional[dict]:
    """读取一条元数据（JSON 解码）。不存在返回 None。"""
    global _conn
    if _conn is None:
        return None
    cursor = _conn.execute("SELECT data FROM meta WHERE key = ?", (key,))
    row = cursor.fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except Exception:
        return None


def set_meta(key: str, data: dict) -> None:
    """按 key 覆盖写入一条元数据（JSON 编码）。"""
    global _conn
    if _conn is None:
        return
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    blob = json.dumps(data, ensure_ascii=False)
    with _lock:
        _conn.execute("""
            INSERT INTO meta (key, data, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at
        """, (key, blob, now))
        _conn.commit()


# ==================== 编辑（供 UI 修改记忆） ====================

def update_event(row_id, *, summary=None, type=None, weight=None,
                 importance=None, embedding=None) -> bool:
    """更新一条 event 的可编辑字段；传 embedding 则一并更新向量。"""
    global _conn
    if _conn is None:
        return False
    sets, vals = [], []
    if summary is not None: sets.append("summary=?"); vals.append(summary)
    if type is not None: sets.append("type=?"); vals.append(type)
    if weight is not None: sets.append("weight=?"); vals.append(weight)
    if importance is not None: sets.append("importance=?"); vals.append(int(importance))
    if embedding is not None: sets.append("embedding=?"); vals.append(_encode_vec(embedding))
    if not sets:
        return False
    vals.append(row_id)
    with _lock:
        cur = _conn.execute(f"UPDATE events SET {', '.join(sets)} WHERE id=?", vals)
        _conn.commit()
        return cur.rowcount > 0


def update_fact(row_id, subject=None, predicate=None, obj=None) -> bool:
    """更新一条 fact 的 SPO 字段。"""
    global _conn
    if _conn is None:
        return False
    sets, vals = [], []
    if subject is not None: sets.append("subject=?"); vals.append(subject)
    if predicate is not None: sets.append("predicate=?"); vals.append(predicate)
    if obj is not None: sets.append("object=?"); vals.append(obj)
    if not sets:
        return False
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    sets.append("updated_at=?"); vals.append(now)
    vals.append(row_id)
    with _lock:
        cur = _conn.execute(f"UPDATE facts SET {', '.join(sets)} WHERE id=?", vals)
        _conn.commit()
        return cur.rowcount > 0


# ==================== 调试与全量管理 ====================

def list_memories(scope: Optional[str] = None, kind: Optional[str] = None) -> list[dict]:
    """系统调试/状态盘点功能"""
    global _conn
    if _conn is None:
        return []
        
    valid_tables = ['events', 'chunks', 'facts', 'summaries']
    tables = [kind] if kind in valid_tables else valid_tables
    results = []
    
    for t in tables:
        query = f"SELECT * FROM {t}"
        params = []
        if scope:
            if t != 'summaries':
                query += " WHERE scope = ?"
                params.append(scope)
            else:
                query += " WHERE key LIKE ?"
                params.append(f"%{scope}%")
                
        cursor = _conn.execute(query, params)
        for r in cursor.fetchall():
            item = _row_to_dict(r)
            item['__table__'] = t
            results.append(item)
    return results


def forget(table: str, row_id: Union[int, str]) -> bool:
    """遗忘机制：物理抹除单条记忆记录"""
    global _conn
    if _conn is None:
        return False
    pk = 'key' if table == 'summaries' else 'id'
    with _lock:
        cursor = _conn.execute(f"DELETE FROM {table} WHERE {pk} = ?", (row_id,))
        _conn.commit()
        return cursor.rowcount > 0


# ==================== 最终上下文拼装核心 ====================

def build_memory_context(scope: str, session_id: str, *, query_vec: Optional[list[float]] = None, 
                         query_text: str = "", top_k: int = 5, recall_n: int = 30) -> str:
    """多级混合检索并动态组装最终注入大模型的 Prompt 文本块"""
    sections = []
    
    # 1. 硬事实段
    facts = get_facts(scope)
    if facts:
        facts_lines = [f"{f['subject']} {f['predicate']} {f['object']}" for f in facts]
        sections.append(f"【硬事实】\n" + "\n".join(facts_lines))
        
    # 2. 角色关系摘要
    arc_summary = get_summary(f"arc:{scope}")
    if arc_summary and arc_summary.strip():
        sections.append(f"【关系】\n{arc_summary.strip()}")
        
    # 3. 临近会话进展
    session_summary = get_summary(f"session:{session_id}")
    if session_summary and session_summary.strip():
        sections.append(f"【近况】\n{session_summary.strip()}")
        
    # 4. 相似回忆提取
    events = recall_events(scope, query_vec=query_vec, query_text=query_text, k=top_k, recall_n=recall_n)
    event_summaries = [e['summary'] for e in events if e.get('summary')]
    if event_summaries:
        sections.append(f"【相关回忆】\n" + "\n".join(event_summaries))
        
    # 5. 精细片段提取（带智能去重逻辑）
    chunks = recall_chunks(scope, query_vec=query_vec, query_text=query_text, k=top_k, recall_n=recall_n)
    valid_chunks = []
    for c in chunks:
        c_text = c.get('text', '')
        if not c_text:
            continue
        # 如果当前细节片段已经是某个高维核心事件摘要的子串，则判定为冗余，跳过。
        if any(c_text in es for es in event_summaries):
            continue
        valid_chunks.append(c_text)
        
    if valid_chunks:
        sections.append(f"【细节】\n" + "\n".join(valid_chunks))
        
    return "\n\n".join(sections) if sections else ""


# ==================== 完备自动化测试集 ====================

if __name__ == "__main__":
    # 使用 Windows 兼容的临时文件初始化测试库
    db_fd, tmp_db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    
    try:
        init_db(tmp_db_path)
        test_scope = "char:许今闻"
        test_session = "session:test_123"
        
        # 1. 模拟导入具备多维权重的事件集
        # 事件一：与[1.0, 0.0, 0.0]高度重合
        upsert_event(
            scope=test_scope, session_id=test_session,
            summary="跟许今闻在初夏的雨中初次相遇",
            type="相遇", weight="核心", importance=5,
            embedding=[1.0, 0.0, 0.0]
        )
        # 事件二：正交向量
        upsert_event(
            scope=test_scope, session_id=test_session,
            summary="许今闻分享了他深藏于阁楼底部的秘密画室",
            type="揭示", weight="主线", importance=4,
            embedding=[0.0, 1.0, 0.0]
        )
        # 事件三：高重合度向量，带激烈情感冲突
        upsert_event(
            scope=test_scope, session_id=test_session,
            summary="因为过去的心结，与许今闻在走廊发生激烈争执",
            type="冲突", weight="转折", importance=3,
            embedding=[0.9, 0.1, 0.0]
        )
        
        # 2. 模拟注入对话细节切片
        add_chunk(test_scope, "雨下得很大，他主动把伞向我倾斜。", session_id=test_session, embedding=[1.0, 0.0, 0.0])
        add_chunk(test_scope, "秘密画室里挂满了未干的向日葵油画。", session_id=test_session, embedding=[0.0, 1.0, 0.0])
        
        # 3. 写入知识图谱硬事实
        upsert_fact(test_scope, "许今闻", "擅长", "古典油画")
        upsert_fact(test_scope, "许今闻", "厌恶", "下雨天")
        
        # 4. 写入高维统计大纲与近况
        upsert_summary(f"arc:{test_scope}", "历经生死考验、相互救赎的宿命契约者。")
        upsert_summary(f"session:{test_session}", "双方刚刚经历了一场情绪宣泄，空气里弥漫着沉默。")
        
        # -------- 断言测试 1: 检验向量空间余弦重排准确度 --------
        target_vec = [1.0, 0.0, 0.0]
        vec_results = recall_events(test_scope, query_vec=target_vec, k=3)
        assert len(vec_results) > 0, "向量召回结果不应为空"
        assert "雨中初次相遇" in vec_results[0]['summary'], "向量 [1,0,0] 匹配最精确的事件应排第一"
        
        # -------- 断言测试 2: 检验向量丢失后的纯文本降级匹配功能 --------
        kw_results = recall_events(test_scope, query_vec=None, query_text="争执", k=3)
        assert len(kw_results) > 0, "文本检索结果不应为空"
        assert "激烈争执" in kw_results[0]['summary'], "降级后带有关键词'争执'的项目应当优先重排在首位"
        
        # -------- 人工审查 3: 渲染打印双模上下文状态 --------
        print("="*20 + " 模式一: 正常向量召回注入 " + "="*20)
        context_with_vec = build_memory_context(test_scope, test_session, query_vec=target_vec)
        print(context_with_vec)
        
        print("\n" + "="*20 + " 模式二: 向量空缺降级检索注入 " + "="*20)
        context_degraded = build_memory_context(test_scope, test_session, query_vec=None, query_text="画室")
        print(context_degraded)
        
        # 清理连接
        close_db()
        print("\nALL TESTS PASSED")
        
    finally:
        # 确保进程安全释放临时文件占用的句柄后再行删除
        if os.path.exists(tmp_db_path):
            try:
                os.remove(tmp_db_path)
            except Exception:
                pass