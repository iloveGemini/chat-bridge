import array
import json
import math
import os
import sqlite3
import tempfile
import threading
import time
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
    return array.array("f", vec).tobytes()


def _decode_vec(blob: Optional[bytes]) -> Optional[list[float]]:
    """将 float32 BLOB 解码为 float 列表"""
    if blob is None:
        return None
    a = array.array("f")
    a.frombytes(blob)
    return list(a)


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """将 SQLite Row 转化为标准字典，自动处理特殊字段"""
    d = dict(row)
    # 向量不进返回 dict：避免把 1024 维 embedding 塞满 HTTP 响应；算分时按需从原始行解码
    d.pop("embedding", None)
    if "caused_by" in d and isinstance(d["caused_by"], str):
        try:
            d["caused_by"] = json.loads(d["caused_by"])
        except Exception:
            d["caused_by"] = []
    return d


def _scope_in_clause(scope):
    """scope 可为单个字符串或字符串列表。返回 (where 片段, 参数元组)。
    空列表会构造出一个永假条件，安全地返回零行。"""
    if isinstance(scope, (list, tuple, set)):
        scopes = [s for s in scope if s]
        if not scopes:
            return ("1=0", ())
        placeholders = ",".join("?" for _ in scopes)
        return (f"scope IN ({placeholders})", tuple(scopes))
    return ("scope = ?", (scope,))


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
          created_at TEXT,
          event_id INTEGER
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

        # 6. 世界书表（静态设定按需召回：always_on 常驻 + 关键词触发）
        _conn.execute("""
        CREATE TABLE IF NOT EXISTS lore (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          scope TEXT NOT NULL,           -- 与记忆同源隔离，如 'sess:<id>'
          title TEXT NOT NULL,
          keys TEXT NOT NULL,            -- JSON 数组，触发词+别名 ["教学楼","走廊"]
          content TEXT NOT NULL,
          priority INTEGER DEFAULT 0,    -- 抢预算时降序排序
          always_on INTEGER DEFAULT 0,   -- 1 = Tier 0，永远注入
          position TEXT DEFAULT 'after', -- before=贴系统头(常驻) / after=贴尾部(召回)
          embedding BLOB,                -- 预留：语义通道用，MVP 不填
          created_at REAL, updated_at REAL
        );
        """)
        _conn.execute("CREATE INDEX IF NOT EXISTS idx_lore_scope ON lore(scope);")

        # 迁移：为 chunks 增加说话人列（细节召回时标注 RP 角色名 / 用户）
        _cols = [r[1] for r in _conn.execute("PRAGMA table_info(chunks)").fetchall()]
        if "speaker" not in _cols:
            _conn.execute("ALTER TABLE chunks ADD COLUMN speaker TEXT;")
        if "event_id" not in _cols:
            _conn.execute("ALTER TABLE chunks ADD COLUMN event_id INTEGER;")
        _cols_lore = [r[1] for r in _conn.execute("PRAGMA table_info(lore)").fetchall()]
        if "position" not in _cols_lore:
            _conn.execute("ALTER TABLE lore ADD COLUMN position TEXT DEFAULT 'after';")

        # 迁移：events 表打 3 个场景时空钢印（向后兼容，旧行为 NULL，召回时由 fallback 兜底）
        _ev_cols = [r[1] for r in _conn.execute("PRAGMA table_info(events)").fetchall()]
        if "scene_id" not in _ev_cols:
            _conn.execute("ALTER TABLE events ADD COLUMN scene_id TEXT;")
        if "time_label" not in _ev_cols:
            _conn.execute("ALTER TABLE events ADD COLUMN time_label TEXT;")
        if "place_label" not in _ev_cols:
            _conn.execute("ALTER TABLE events ADD COLUMN place_label TEXT;")

        # 迁移：facts 表打核心状态锁死标记（is_state=1 容量清理时免疫删除）
        _fact_cols = [
            r[1] for r in _conn.execute("PRAGMA table_info(facts)").fetchall()
        ]
        if "is_state" not in _fact_cols:
            _conn.execute("ALTER TABLE facts ADD COLUMN is_state INTEGER DEFAULT 0;")

        # 迁移：lore 表加 last_surfaced（主动召回冷却用：上次被「主动提起」的时间戳）
        _lore_cols = [r[1] for r in _conn.execute("PRAGMA table_info(lore)").fetchall()]
        if "last_surfaced" not in _lore_cols:
            _conn.execute("ALTER TABLE lore ADD COLUMN last_surfaced REAL DEFAULT 0;")

        # 7. 世界书容器表（独立实体，可绑角色/绑用户/不绑；条目挂在 scope='wb:<id>' 下）
        _conn.execute("""
        CREATE TABLE IF NOT EXISTS worldbooks (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT NOT NULL,
          bind_type TEXT NOT NULL DEFAULT 'none',   -- 'character' | 'user' | 'none'
          bind_target TEXT DEFAULT '',              -- 角色 key / 用户 key / ''
          created_at REAL, updated_at REAL
        );
        """)

        # 8. 记忆表（录入记忆）
        _conn.execute("""
        CREATE TABLE IF NOT EXISTS memories (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          scope TEXT NOT NULL,
          folder TEXT,
          content TEXT NOT NULL,
          is_resident INTEGER DEFAULT 0,
          embedding BLOB,
          created_at TEXT
        );
        """)
        _conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope);")

        # 9. 聊天记录表（messages）
        _conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id TEXT NOT NULL,
          role TEXT NOT NULL,
          type TEXT,
          tool_name TEXT,
          text TEXT,
          ts REAL,
          scene_id TEXT,
          time TEXT,
          place TEXT,
          raw_json TEXT
        );
        """)
        _conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);")
        # 一次性迁移：旧世界书条目绑在会话 scope（sess:*）上，新模型改绑世界书。
        # 按产品决策「清空重来」，把残留的会话级 lore 物理清除（只清 sess:*，wb:* 保留）。
        # 注意：此处仍持有 _lock，不能调用会再次加锁的 set_meta/get_meta，直接走 _conn。
        try:
            _mig = _conn.execute(
                "SELECT data FROM meta WHERE key='lore_wb_migrated'"
            ).fetchone()
            if not _mig:
                _conn.execute("DELETE FROM lore WHERE scope LIKE 'sess:%'")
                _conn.execute(
                    "INSERT OR REPLACE INTO meta (key, data, updated_at) VALUES (?,?,?)",
                    ("lore_wb_migrated", "1", time.strftime("%Y-%m-%d %H:%M:%S")),
                )
        except Exception:
            pass

        _conn.commit()


def close_db() -> None:
    """关闭数据库连接"""
    global _conn
    with _lock:
        if _conn:
            _conn.close()
            _conn = None


# ==================== 写入数据原语 ====================


def upsert_event(
    scope: str,
    summary: str,
    *,
    session_id: Optional[str] = None,
    type: Optional[str] = None,
    weight: Optional[str] = None,
    caused_by: Optional[list] = None,
    embedding: Optional[list[float]] = None,
    importance: int = 3,
    msg_start: Optional[int] = None,
    msg_end: Optional[int] = None,
    scene_id: Optional[str] = None,
    time_label: Optional[str] = None,
    place_label: Optional[str] = None,
) -> int:
    """保存或追加重要事件记录，返回自增 ID。scene_id/time_label/place_label 为剧情时空锚点。"""
    global _conn
    if _conn is None:
        raise RuntimeError("Database not initialized. Call init_db first.")

    now = time.strftime("%Y-%m-%d %H:%M:%S")
    caused_by_str = json.dumps(caused_by) if caused_by is not None else "[]"
    blob = _encode_vec(embedding)

    with _lock:
        cursor = _conn.cursor()
        cursor.execute(
            """
            INSERT INTO events (
                scope, session_id, msg_start, msg_end, type, weight,
                summary, caused_by, embedding, importance, created_at, last_seen_at, hits,
                scene_id, time_label, place_label
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
        """,
            (
                scope,
                session_id,
                msg_start,
                msg_end,
                type,
                weight,
                summary,
                caused_by_str,
                blob,
                importance,
                now,
                now,
                scene_id,
                time_label,
                place_label,
            ),
        )
        _conn.commit()
        return cursor.lastrowid


def add_chunk(
    scope: str,
    text: str,
    *,
    session_id: Optional[str] = None,
    msg_floor: Optional[int] = None,
    embedding: Optional[list[float]] = None,
    speaker: Optional[str] = None,
    event_id: Optional[int] = None,
) -> int:
    """添加一条聊天切片细节（speaker 为说话人标签：RP 角色名 / 用户）"""
    global _conn
    if _conn is None:
        raise RuntimeError("Database not initialized.")

    now = time.strftime("%Y-%m-%d %H:%M:%S")
    blob = _encode_vec(embedding)

    with _lock:
        cursor = _conn.cursor()
        cursor.execute(
            """
            INSERT INTO chunks (scope, session_id, msg_floor, text, embedding, created_at, speaker, event_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (scope, session_id, msg_floor, text, blob, now, speaker, event_id),
        )
        _conn.commit()
        return cursor.lastrowid


def upsert_fact(
    scope: str,
    subject: str,
    predicate: str,
    obj: str = "",
    is_state: bool = False,
    retracted: bool = False,
) -> None:
    """按 (scope, subject, predicate) 覆盖或写入硬事实。
    retracted=True：旧事实失效，静默删除该 (scope,subject,predicate)。
    is_state=True：世界观公理/角色设定/绝对禁忌/底层称呼，容量清理时免疫删除。"""
    global _conn
    if _conn is None:
        raise RuntimeError("Database not initialized.")

    # 分支 A：静默抹杀（旧事实被推翻 / 失效）
    if retracted:
        with _lock:
            _conn.execute(
                "DELETE FROM facts WHERE scope=? AND subject=? AND predicate=?",
                (scope, subject, predicate),
            )
            _conn.commit()
        return

    # 分支 B：更新或插入，带核心状态钢印
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    state_int = 1 if is_state else 0
    with _lock:
        _conn.execute(
            """
            INSERT INTO facts (scope, subject, predicate, object, is_state, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope, subject, predicate) DO UPDATE SET
                object = excluded.object,
                is_state = excluded.is_state,
                updated_at = excluded.updated_at
        """,
            (scope, subject, predicate, obj, state_int, now),
        )
        _conn.commit()


def prune_facts(scope: str, max_limit: int = 80) -> int:
    """容量卫士：该角色事实数突破 max_limit 时，静默斩首最古老的非核心事实(is_state=0)。
    返回实际删除条数。is_state=1 的核心设定绝对免疫。"""
    global _conn
    if _conn is None:
        return 0
    with _lock:
        count = _conn.execute(
            "SELECT count(*) FROM facts WHERE scope=?", (scope,)
        ).fetchone()[0]
        if count <= max_limit:
            return 0
        overflow = count - max_limit + 5  # 多删 5 条留缓冲
        cur = _conn.execute(
            """
            DELETE FROM facts
            WHERE rowid IN (
                SELECT rowid FROM facts
                WHERE scope=? AND is_state=0
                ORDER BY updated_at ASC LIMIT ?
            )
        """,
            (scope, overflow),
        )
        _conn.commit()
        return cur.rowcount


def upsert_summary(key: str, text: str) -> None:
    """按 key 覆盖全局大纲或会话近况摘要"""
    global _conn
    if _conn is None:
        raise RuntimeError("Database not initialized.")

    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        _conn.execute(
            """
            INSERT INTO summaries (key, text, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                text = excluded.text,
                updated_at = excluded.updated_at
        """,
            (key, text, now),
        )
        _conn.commit()


# ==================== 记忆检索原语 ====================


def recall_events(
    scope: str,
    *,
    query_vec: Optional[list[float]] = None,
    query_text: str = "",
    k: int = 5,
    recall_n: int = 30,
) -> list[dict]:
    """高精度/降级检索核心事件"""
    global _conn
    if _conn is None:
        return []

    # 场景 A: 基于向量的召回 + 权重调节排序
    if query_vec is not None:
        cursor = _conn.execute(
            "SELECT * FROM events WHERE scope = ? AND embedding IS NOT NULL", (scope,)
        )
        rows = cursor.fetchall()

        scored_items = []
        for r in rows:
            d = _row_to_dict(r)
            d["score"] = _cosine(query_vec, _decode_vec(r["embedding"]))
            scored_items.append(d)

        # 先按原始余弦相似度进行高位截断召回
        scored_items.sort(key=lambda x: x["score"], reverse=True)
        top_recalled = scored_items[:recall_n]

        # 配合角色设定权重系数进行二次重排
        weight_map = {"核心": 1.3, "主线": 1.15, "转折": 1.1, "点睛": 1.0, "氛围": 0.9}

        def _final_rank_key(item):
            w = weight_map.get(item.get("weight"), 1.0)
            return item["score"] * w

        top_recalled.sort(key=_final_rank_key, reverse=True)
        return top_recalled[:k]

    # 场景 B: 降级到文本或纯时间排序
    else:
        cursor = _conn.execute("SELECT * FROM events WHERE scope = ?", (scope,))
        rows = [_row_to_dict(r) for r in cursor.fetchall()]
        words = [w for w in query_text.split() if w]

        if words:

            def _calc_match_hit(item):
                summary = item.get("summary", "")
                return 1 if any(w in summary for w in words) else 0

            # 排序策略：命中关键词优先 > 重要度降序 > 时间降序
            rows.sort(
                key=lambda x: (
                    _calc_match_hit(x),
                    x.get("importance", 3),
                    x.get("created_at", ""),
                ),
                reverse=True,
            )
            return rows[:k]
        else:
            # 纯时间线降级
            rows.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            return rows[:k]


def recall_chunks(
    scope: str,
    *,
    query_vec: Optional[list[float]] = None,
    query_text: str = "",
    k: int = 5,
    recall_n: int = 30,
) -> list[dict]:
    """细节切片检索层"""
    global _conn
    if _conn is None:
        return []

    if query_vec is not None:
        cursor = _conn.execute(
            "SELECT * FROM chunks WHERE scope = ? AND embedding IS NOT NULL", (scope,)
        )
        rows = cursor.fetchall()

        scored_items = []
        for r in rows:
            d = _row_to_dict(r)
            d["score"] = _cosine(query_vec, _decode_vec(r["embedding"]))
            scored_items.append(d)

        scored_items.sort(key=lambda x: x["score"], reverse=True)
        return scored_items[:recall_n][:k]
    else:
        cursor = _conn.execute("SELECT * FROM chunks WHERE scope = ?", (scope,))
        rows = [_row_to_dict(r) for r in cursor.fetchall()]
        words = [w for w in query_text.split() if w]

        if words:

            def _calc_match_hit(item):
                text = item.get("text", "")
                return 1 if any(w in text for w in words) else 0

            rows.sort(
                key=lambda x: (_calc_match_hit(x), x.get("created_at", "")),
                reverse=True,
            )
            return rows[:k]
        else:
            rows.sort(key=lambda x: x.get("created_at", ""), reverse=True)
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
        _conn.execute(
            """
            INSERT INTO meta (key, data, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at
        """,
            (key, blob, now),
        )
        _conn.commit()


# ==================== 编辑（供 UI 修改记忆） ====================


def update_event(
    row_id, *, summary=None, type=None, weight=None, importance=None, embedding=None
) -> bool:
    """更新一条 event 的可编辑字段；传 embedding 则一并更新向量。"""
    global _conn
    if _conn is None:
        return False
    sets, vals = [], []
    if summary is not None:
        sets.append("summary=?")
        vals.append(summary)
    if type is not None:
        sets.append("type=?")
        vals.append(type)
    if weight is not None:
        sets.append("weight=?")
        vals.append(weight)
    if importance is not None:
        sets.append("importance=?")
        vals.append(int(importance))
    if embedding is not None:
        sets.append("embedding=?")
        vals.append(_encode_vec(embedding))
    if not sets:
        return False
    vals.append(row_id)
    with _lock:
        cur = _conn.execute(f"UPDATE events SET {', '.join(sets)} WHERE id=?", vals)
        _conn.commit()
        return cur.rowcount > 0


def update_fact(row_id, subject=None, predicate=None, obj=None, is_state=None) -> bool:
    """更新一条 fact 的 SPO 字段及核心状态锁(is_state)。"""
    global _conn
    if _conn is None:
        return False
    sets, vals = [], []
    if subject is not None:
        sets.append("subject=?")
        vals.append(subject)
    if predicate is not None:
        sets.append("predicate=?")
        vals.append(predicate)
    if obj is not None:
        sets.append("object=?")
        vals.append(obj)
    if is_state is not None:
        sets.append("is_state=?")
        vals.append(1 if is_state else 0)
    if not sets:
        return False
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    sets.append("updated_at=?")
    vals.append(now)
    vals.append(row_id)
    with _lock:
        cur = _conn.execute(f"UPDATE facts SET {', '.join(sets)} WHERE id=?", vals)
        _conn.commit()
        return cur.rowcount > 0


# ==================== 调试与全量管理 ====================


def list_memories(
    scope: Optional[str] = None, kind: Optional[str] = None
) -> list[dict]:
    """系统调试/状态盘点功能"""
    global _conn
    if _conn is None:
        return []

    valid_tables = ["events", "chunks", "facts", "summaries", "memories"]
    tables = [kind] if kind in valid_tables else valid_tables
    results = []

    for t in tables:
        query = f"SELECT * FROM {t}"
        params = []
        if scope:
            if t != "summaries":
                query += " WHERE scope = ?"
                params.append(scope)
            else:
                # 关系弧按精确 key 命中，避免 sess:A 误配 sess:A2 这类前缀重叠
                query += " WHERE key = ?"
                params.append(f"arc:{scope}")

        cursor = _conn.execute(query, params)
        for r in cursor.fetchall():
            item = _row_to_dict(r)
            item["__table__"] = t
            results.append(item)
    return results


def forget(table: str, row_id: Union[int, str]) -> bool:
    """遗忘机制：物理抹除单条记忆记录"""
    global _conn
    if _conn is None:
        return False
    pk = "key" if table == "summaries" else "id"
    with _lock:
        cursor = _conn.execute(f"DELETE FROM {table} WHERE {pk} = ?", (row_id,))
        _conn.commit()
        return cursor.rowcount > 0


# ==================== 作用域级操作（克隆 fork / 重命名 migrate / 删除清理）====================


def _copy_scope_rows(
    table: str, src_scope: str, dst_scope: str, dst_session_id: Optional[str]
) -> None:
    """把某表 src_scope 下的所有行复制为 dst_scope（覆盖 scope/session_id 列），保留 embedding 等其余字段。"""
    cols = [r[1] for r in _conn.execute(f"PRAGMA table_info({table})").fetchall()]
    insert_cols = [c for c in cols if c != "id"]
    select_exprs, params = [], []
    for c in insert_cols:
        if c == "scope":
            select_exprs.append("?")
            params.append(dst_scope)
        elif c == "session_id":
            select_exprs.append("?")
            params.append(dst_session_id)
        else:
            select_exprs.append(c)
    params.append(src_scope)
    _conn.execute(
        f"INSERT INTO {table} ({', '.join(insert_cols)}) "
        f"SELECT {', '.join(select_exprs)} FROM {table} WHERE scope = ?",
        params,
    )


def fork_scope(
    src_scope: str, dst_scope: str, src_session_id: str, dst_session_id: str
) -> dict:
    """克隆：把 src 的全部记忆复制一份独立副本到 dst（之后互不影响）。
    复制 events/chunks/facts（按 scope）+ 关系弧/会话近况/总结边界（按 key）。"""
    global _conn
    if _conn is None:
        return {"ok": False, "error": "db not init"}
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        for t in ("events", "chunks", "facts"):
            _copy_scope_rows(t, src_scope, dst_scope, dst_session_id)
        # 关系弧 arc:<scope>
        arc = _conn.execute(
            "SELECT text FROM summaries WHERE key=?", (f"arc:{src_scope}",)
        ).fetchone()
        if arc:
            _conn.execute(
                "INSERT OR REPLACE INTO summaries (key, text, updated_at) VALUES (?,?,?)",
                (f"arc:{dst_scope}", arc[0], now),
            )
        # 会话近况 session:<session_id>
        sess = _conn.execute(
            "SELECT text FROM summaries WHERE key=?", (f"session:{src_session_id}",)
        ).fetchone()
        if sess:
            _conn.execute(
                "INSERT OR REPLACE INTO summaries (key, text, updated_at) VALUES (?,?,?)",
                (f"session:{dst_session_id}", sess[0], now),
            )
        # 总结边界 summ:<session_id>
        m = _conn.execute(
            "SELECT data FROM meta WHERE key=?", (f"summ:{src_session_id}",)
        ).fetchone()
        if m:
            _conn.execute(
                "INSERT OR REPLACE INTO meta (key, data, updated_at) VALUES (?,?,?)",
                (f"summ:{dst_session_id}", m[0], now),
            )
        _conn.commit()
    return {"ok": True}


def migrate_scope(
    src_scope: str, dst_scope: str, src_session_id: str, dst_session_id: str
) -> dict:
    """重命名：把 src 的记忆就地迁移到 dst（同一份，不复制），用于会话改名后保持记忆不丢。"""
    global _conn
    if _conn is None:
        return {"ok": False, "error": "db not init"}
    if src_scope == dst_scope and src_session_id == dst_session_id:
        return {"ok": True}
    with _lock:
        for t in ("events", "chunks"):
            _conn.execute(
                f"UPDATE {t} SET scope=?, session_id=? WHERE scope=?",
                (dst_scope, dst_session_id, src_scope),
            )
        _conn.execute("UPDATE messages SET session_id=? WHERE session_id=?", (dst_session_id, src_session_id))
        _conn.execute("UPDATE facts SET scope=? WHERE scope=?", (dst_scope, src_scope))
        _conn.execute("UPDATE memories SET scope=? WHERE scope=?", (dst_scope, src_scope))
        _conn.execute("UPDATE lore SET scope=? WHERE scope=?", (dst_scope, src_scope))
        _conn.execute(
            "UPDATE summaries SET key=? WHERE key=?",
            (f"arc:{dst_scope}", f"arc:{src_scope}"),
        )
        _conn.execute(
            "UPDATE summaries SET key=? WHERE key=?",
            (f"session:{dst_session_id}", f"session:{src_session_id}"),
        )
        _conn.execute(
            "UPDATE meta SET key=? WHERE key=?",
            (f"summ:{dst_session_id}", f"summ:{src_session_id}"),
        )
        _conn.commit()
    return {"ok": True}


def delete_scope(scope: str, session_id: str) -> dict:
    """删除会话时清掉它的全部记忆，避免孤儿数据堆积。"""
    global _conn
    if _conn is None:
        return {"ok": False, "error": "db not init"}
    with _lock:
        for t in ("events", "chunks", "facts"):
            _conn.execute(f"DELETE FROM {t} WHERE scope=?", (scope,))
        _conn.execute(
            "DELETE FROM summaries WHERE key IN (?, ?)",
            (f"arc:{scope}", f"session:{session_id}"),
        )
        _conn.execute("DELETE FROM meta WHERE key=?", (f"summ:{session_id}",))
        _conn.commit()
    reset_lore_warm(session_id)
    return {"ok": True}


def add_memory(scope: str, content: str, folder: str = "", is_resident: int = 0) -> int:
    """添加一条录入记忆"""
    with _lock:
        cur = _conn.execute(
            "INSERT INTO memories (scope, folder, content, is_resident, created_at) VALUES (?, ?, ?, ?, ?)",
            (scope, folder, content, is_resident, time.strftime("%Y-%m-%d %H:%M:%S"))
        )
        _conn.commit()
        return cur.lastrowid

def get_memories(scope: str) -> list[dict]:
    """获取指定 scope 下的所有录入记忆"""
    with _lock:
        where, params = _scope_in_clause(scope)
        rows = _conn.execute(f"SELECT * FROM memories WHERE {where} ORDER BY id ASC", params).fetchall()
        return [_row_to_dict(r) for r in rows]

def rename_memory_folder(scope: str, old_folder: str, new_folder: str) -> bool:
    """将指定 scope 下 folder 为 old_folder 的所有记录的 folder 更新为 new_folder"""
    with _lock:
        where, params = _scope_in_clause(scope)
        cur = _conn.execute(
            f"UPDATE memories SET folder = ? WHERE {where} AND folder = ?",
            (new_folder, *params, old_folder)
        )
        _conn.commit()
        return cur.rowcount > 0

def delete_memory_folder(scope: str, folder: str) -> bool:
    """删除指定 scope 下 folder 为 folder 的所有记录"""
    with _lock:
        where, params = _scope_in_clause(scope)
        cur = _conn.execute(
            f"DELETE FROM memories WHERE {where} AND folder = ?",
            (*params, folder)
        )
        _conn.commit()
        return cur.rowcount > 0

def set_memory_folder_resident(scope: str, folder: str, is_resident: int) -> bool:
    """将指定 scope 下 folder 为 folder 的所有记录的 is_resident 更新为 is_resident"""
    with _lock:
        where, params = _scope_in_clause(scope)
        cur = _conn.execute(
            f"UPDATE memories SET is_resident = ? WHERE {where} AND folder = ?",
            (is_resident, *params, folder)
        )
        _conn.commit()
        return cur.rowcount > 0
# ==================== 聊天记录（messages） ====================

def get_messages(session_id: str) -> list[dict]:
    """获取指定 session_id 的所有聊天记录"""
    global _conn
    if _conn is None:
        return []
    with _lock:
        rows = _conn.execute(
            "SELECT raw_json FROM messages WHERE session_id = ? ORDER BY id ASC",
            (session_id,)
        ).fetchall()
        return [json.loads(r[0]) for r in rows if r[0]]

def save_messages(session_id: str, messages: list[dict]) -> None:
    """全量覆盖保存指定 session_id 的聊天记录"""
    global _conn
    if _conn is None:
        return
    with _lock:
        _conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        for msg in messages:
            _conn.execute(
                """
                INSERT INTO messages (
                    session_id, role, type, tool_name, text, ts, scene_id, time, place, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    msg.get("role", ""),
                    msg.get("type", ""),
                    msg.get("tool_name", ""),
                    msg.get("text", ""),
                    msg.get("ts", 0.0),
                    msg.get("scene_id", ""),
                    msg.get("time", ""),
                    msg.get("place", ""),
                    json.dumps(msg, ensure_ascii=False)
                )
            )
        _conn.commit()

# ==================== 最终上下文拼装核心 ====================
# §3 记忆段→注入位置映射（可调）。before=贴系统头(常驻)，after=贴尾部(召回)。
# lore(世界书)按每条 position 字段独立分桶，不在此表。改这里即可调整某段的归属。
SECTION_POSITION = {
    "fact_graph": "before",  # 硬事实：常驻
    "relation_arc": "before",  # 关系弧：常驻
    "recent_state": "before",  # 会话近况(当前状态)：常驻
    "episodic_memory_chain": "after",  # 相似回忆：本轮召回
    "original_dialogue": "after",  # 原文细节：本轮召回
    "normal_memories": "after",  # 录入记忆：本轮召回
}


def build_memory_context(
    scope: str,
    session_id: str,
    *,
    query_vec: Optional[list[float]] = None,
    query_text: str = "",
    top_k: int = 5,
    recall_n: int = 30,
    lore_scan: str = "",
    lore_sem_topk: int = 3,
    lore_sem_threshold: float = 0.40,
    lore_warm_rounds: int = 0,
    lore_scopes: Optional[list] = None,
    before_out: Optional[list] = None,
    diag: Optional[dict] = None,
) -> str:
    """多级混合检索并动态组装最终注入大模型的 Prompt 文本块。
    传入 diag={} 可拿到本次召回的可观测诊断（模式/分数/命中/去重），用于日志。
    lore_scan：世界书扫描文本（当前消息+近况+场景地点/时间），命中才注入设定。"""
    sections = []

    def _emit_section(name, block):
        # 段级位置路由；before_out 缺省时回落 after，避免不分桶的旧调用丢内容。
        if SECTION_POSITION.get(name, "after") == "before" and before_out is not None:
            before_out.append(block)
        else:
            sections.append(block)

    # 0. 世界书段（静态设定，舞台背景，放最前）：always_on 常驻 + 关键词触发
    # 世界书已与记忆 scope 解耦：lore_scopes 为本会话适用的多本世界书 scope（绑角色/绑用户/手动挂）。
    # 兼容旧调用：未显式传 lore_scopes 时退回记忆 scope。
    _lore_scope = lore_scopes if lore_scopes is not None else scope
    lore_hits = recall_lore(
        _lore_scope,
        lore_scan,
        query_vec=query_vec,
        sem_topk=lore_sem_topk,
        sem_threshold=lore_sem_threshold,
        session_id=session_id,
        warm_rounds=lore_warm_rounds,
        diag=diag,
    )
    if lore_hits:
        # §3 统一注入：lore 按 position 分桶。before→系统头(常驻)，after(默认)→尾部(召回)。
        _before = [e for e in lore_hits if (e.get("position") or "after") == "before"]
        _after = [e for e in lore_hits if (e.get("position") or "after") != "before"]
        if _after:
            sections.append(
                "<world_book>\n"
                + "\n".join(f"【{e['title']}】{e['content']}" for e in _after)
                + "\n</world_book>"
            )
        if _before and before_out is not None:
            before_out.append(
                "<world_book>\n"
                + "\n".join(f"【{e['title']}】{e['content']}" for e in _before)
                + "\n</world_book>"
            )

    # 召回模式：有查询向量走向量检索，否则降级关键词/时间线
    mode = "vector" if query_vec is not None else "keyword"
    words = [w for w in (query_text or "").split() if w]

    def _hit(text):
        # 关键词模式下才有"命中"概念；向量模式返回 None（看分数即可）
        if mode != "keyword" or not words:
            return None
        return any(w in (text or "") for w in words)

    if diag is not None:
        diag.update(
            {
                "scope": scope,
                "mode": mode,
                "query": (query_text or "")[:40],
                "events": [],
                "chunks": [],
            }
        )

    # 1. 硬事实段
    facts = get_facts(scope)
    if facts:
        facts_lines = [f"{f['subject']} {f['predicate']} {f['object']}" for f in facts]
        _emit_section(
            "fact_graph", "<fact_graph>\n" + "\n".join(facts_lines) + "\n</fact_graph>"
        )
    if diag is not None:
        diag["facts"] = len(facts)

    # 1.5 录入记忆段
    memories = get_memories(scope)
    if memories:
        resident_mems = [m for m in memories if m.get("is_resident") == 1]
        normal_mems = [m for m in memories if m.get("is_resident") == 0]
        
        if resident_mems and before_out is not None:
            before_out.append(
                "<resident_memories>\n"
                + "\n".join(f"【{m['folder']}】{m['content']}" if m.get("folder") else m['content'] for m in resident_mems)
                + "\n</resident_memories>"
            )
        if normal_mems:
            _emit_section(
                "normal_memories",
                "<memories>\n"
                + "\n".join(f"【{m['folder']}】{m['content']}" if m.get("folder") else m['content'] for m in normal_mems)
                + "\n</memories>"
            )
    # 2. 角色关系摘要
    arc_summary = get_summary(f"arc:{scope}")
    if arc_summary and arc_summary.strip():
        _emit_section(
            "relation_arc",
            "<relation_arc>\n" + arc_summary.strip() + "\n</relation_arc>",
        )
    if diag is not None:
        diag["arc"] = bool(arc_summary and arc_summary.strip())

    # 3. 临近会话进展
    session_summary = get_summary(f"session:{session_id}")
    if session_summary and session_summary.strip():
        _emit_section(
            "recent_state",
            "<recent_state>\n" + session_summary.strip() + "\n</recent_state>",
        )
    if diag is not None:
        diag["session"] = bool(session_summary and session_summary.strip())

    # 4. 相似回忆提取
    events = recall_events(
        scope, query_vec=query_vec, query_text=query_text, k=top_k, recall_n=recall_n
    )
    event_summaries = [e["summary"] for e in events if e.get("summary")]
    if event_summaries:
        _emit_section(
            "episodic_memory_chain",
            "<episodic_memory_chain>\n"
            + "\n".join(event_summaries)
            + "\n</episodic_memory_chain>",
        )
    if diag is not None:
        for e in events:
            s = e.get("summary", "")
            diag["events"].append(
                {
                    "score": e.get("score"),
                    "weight": e.get("weight"),
                    "hit": _hit(s),
                    "snippet": s[:32],
                }
            )

    # 5. 精细片段提取（带智能去重逻辑）
    chunks = recall_chunks(
        scope, query_vec=query_vec, query_text=query_text, k=top_k, recall_n=recall_n
    )
    valid_chunks = []
    for c in chunks:
        c_text = c.get("text", "")
        if not c_text:
            continue
        # 如果当前细节片段已经是某个高维核心事件摘要的子串，则判定为冗余，跳过。
        dropped = any(c_text in es for es in event_summaries)
        if diag is not None:
            diag["chunks"].append(
                {
                    "score": c.get("score"),
                    "speaker": (c.get("speaker") or "").strip(),
                    "hit": _hit(c_text),
                    "dropped": dropped,
                    "snippet": c_text[:32],
                }
            )
        if dropped:
            continue
        spk = (c.get("speaker") or "").strip()
        valid_chunks.append(f"{spk}：{c_text}" if spk else c_text)

    if valid_chunks:
        _emit_section(
            "original_dialogue",
            "<original_dialogue>\n"
            + "\n".join(valid_chunks)
            + "\n</original_dialogue>",
        )

    if diag is not None:
        diag["empty"] = len(sections) == 0

    return "\n\n".join(sections) if sections else ""


def format_recall_log(diag: dict) -> str:
    """把 build_memory_context 的 diag 渲染成高度结构化的 ASCII 树状日志。"""
    if not diag:
        return "🧠 [记忆召回] 无诊断数据"

    def _clean(s, max_l=26):
        t = " ".join((s or "").split())
        return t[:max_l] + ("..." if len(t) > max_l else "")

    def _mark(it):
        if it.get("score") is not None:
            return f"[{it['score']:.2f}]"
        h = it.get("hit")
        return "[kw ✓]" if h else ("[kw ✗]" if h is False else "[—]")

    evs, chs = diag.get("events", []), diag.get("chunks", [])
    kept = [c for c in chs if not c.get("dropped")]

    q_str = _clean(diag.get("query", ""), 16)
    head = (
        f'🧠 [记忆召回·{diag.get("mode")}] q="{q_str}" '
        f"│ 事实:{diag.get('facts', 0)} 关系:{'✓' if diag.get('arc') else '✗'} 近况:{'✓' if diag.get('session') else '✗'} "
        f"│ 回忆:{len(evs)} 细节:{len(kept)} (去重丢弃:{len(chs) - len(kept)})"
        + (" ⚠️ 空召回" if diag.get("empty") else "")
    )

    lines = [head]
    lore = diag.get("lore", [])
    if lore:

        def _lore_tag(lore_item):
            via = lore_item.get("via", "keyword")
            if via == "always":
                return "[常驻]"
            if via == "semantic":
                return (
                    f"[语义 {lore_item.get('score'):.2f}]"
                    if lore_item.get("score") is not None
                    else "[语义]"
                )
            if via == "warm":
                return (
                    f"[warm +{lore_item.get('age')}]"
                    if lore_item.get("age") is not None
                    else "[warm]"
                )
            kh = lore_item.get("keys_hit") or []
            return f"[词:{','.join(kh[:2])}]" if kh else "[词]"

        lines.append(f"   📖 世界书命中:{len(lore)}")
        for lore_item in lore:
            lines.append(
                f"      ├─ {_lore_tag(lore_item)} {_clean(lore_item.get('title', ''), 20)}"
            )
    for i, e in enumerate(evs, 1):
        w = f"({e['weight']})" if e.get("weight") else ""
        lines.append(f"   ├─ 回忆{i} {_mark(e)}{w} {_clean(e.get('snippet', ''), 24)}")

    for i, c in enumerate(chs, 1):
        is_last = i == len(chs)
        prefix = "   └─ " if is_last else "   ├─ "
        spk = c.get("speaker", "").strip()
        spk_tag = f"[{spk}]: " if spk else ""

        snippet_text = _clean(c.get("snippet", ""), 22)
        if c.get("dropped"):
            lines.append(f"{prefix}细节{i} [✗ 去重] {spk_tag}{snippet_text}")
        else:
            lines.append(f"{prefix}细节{i} {_mark(c)} {spk_tag}{snippet_text}")

    return "\n".join(lines)


# ==================== 世界书（World Book / Lorebook）====================


def _lore_row_to_dict(
    row: sqlite3.Row, *, keep_embedding: bool = False
) -> dict[str, Any]:
    d = dict(row)
    d["has_embedding"] = d.get("embedding") is not None
    if not keep_embedding:
        d.pop("embedding", None)
    try:
        d["keys"] = json.loads(d.get("keys") or "[]")
    except Exception:
        d["keys"] = []
    return d


def add_lore(
    scope: str,
    title: str,
    content: str,
    *,
    keys: Optional[list[str]] = None,
    priority: int = 0,
    always_on: bool = False,
    position: str = "after",
    embedding: Optional[list[float]] = None,
) -> int:
    """新增一条世界书设定。keys 为触发词数组；always_on=True 则常驻。返回 id。"""
    now = time.time()
    with _lock:
        cur = _conn.execute(
            """
            INSERT INTO lore (scope, title, keys, content, priority, always_on,
                              position, embedding, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
            (
                scope,
                title,
                json.dumps(keys or [], ensure_ascii=False),
                content,
                int(priority),
                1 if always_on else 0,
                ("before" if position == "before" else "after"),
                _encode_vec(embedding),
                now,
                now,
            ),
        )
        _conn.commit()
        return cur.lastrowid


def list_lore(scope: str) -> list[dict]:
    """列出某 scope 下全部世界书条目（priority 降序），供 UI 管理。"""
    cur = _conn.execute(
        "SELECT * FROM lore WHERE scope=? ORDER BY always_on DESC, priority DESC, id ASC",
        (scope,),
    )
    return [_lore_row_to_dict(r) for r in cur.fetchall()]


def update_lore(
    row_id,
    *,
    title=None,
    content=None,
    keys=None,
    priority=None,
    always_on=None,
    position=None,
    embedding=None,
) -> bool:
    """部分更新一条世界书条目。只改传入的字段。"""
    sets, params = [], []
    if title is not None:
        sets.append("title=?")
        params.append(title)
    if content is not None:
        sets.append("content=?")
        params.append(content)
    if keys is not None:
        sets.append("keys=?")
        params.append(json.dumps(keys, ensure_ascii=False))
    if priority is not None:
        sets.append("priority=?")
        params.append(int(priority))
    if always_on is not None:
        sets.append("always_on=?")
        params.append(1 if always_on else 0)
    if position is not None:
        sets.append("position=?")
        params.append("before" if position == "before" else "after")
    if embedding is not None:
        sets.append("embedding=?")
        params.append(_encode_vec(embedding))
    if not sets:
        return False
    sets.append("updated_at=?")
    params.append(time.time())
    params.append(row_id)
    with _lock:
        cur = _conn.execute(f"UPDATE lore SET {', '.join(sets)} WHERE id=?", params)
        _conn.commit()
        return cur.rowcount > 0


def delete_lore(row_id) -> bool:
    with _lock:
        cur = _conn.execute("DELETE FROM lore WHERE id=?", (row_id,))
        _conn.commit()
        return cur.rowcount > 0


# 迟滞防抖状态：session_id -> {"round": 轮次, "hit": {entry_id: 最后命中轮次}}
# 关键词/语义命中过的条目在 warm_rounds 轮内即使本轮没命中也保留，避免「忽然忘事」。
_LORE_WARM: dict[str, dict] = {}


def reset_lore_warm(session_id: Optional[str] = None) -> None:
    """清空迟滞状态。不传 session_id 则全清（主要给测试/会话删除用）。"""
    if session_id is None:
        _LORE_WARM.clear()
    else:
        _LORE_WARM.pop(session_id, None)


def recall_lore(
    scope: str,
    scan_text: str,
    *,
    query_vec: Optional[list[float]] = None,
    sem_topk: int = 3,
    sem_threshold: float = 0.40,
    budget_chars: Optional[int] = None,
    session_id: Optional[str] = None,
    warm_rounds: int = 0,
    diag: Optional[dict] = None,
) -> list[dict]:
    """三通道召回 + 迟滞防抖：always_on 常驻 + 关键词命中 + 语义 top-k 兜底 + warm 保留。
    - scan_text：当前消息+近况+场景地点/时间，用于关键词扫描。
    - query_vec：查询向量；提供时对未命中关键词的条目做余弦召回，
      取相似度 ≥ sem_threshold 的前 sem_topk 条（专有名词靠关键词，模糊场景靠语义）。
    - warm_rounds>0 且给了 session_id：关键词/语义命中过的条目，在随后 warm_rounds 轮内
      即使本轮未命中也保留（迟滞），减少条目抖动；always_on 不受影响。
    - 命中条目按 (always_on, priority, id) 排序；可选 budget_chars 截断。
    返回 dict 列表（含 title/content/keys/priority/always_on）。"""
    _clause, _params = _scope_in_clause(scope)
    rows = [
        _lore_row_to_dict(r, keep_embedding=True)
        for r in _conn.execute(
            f"SELECT * FROM lore WHERE {_clause}", _params
        ).fetchall()
    ]
    scan = scan_text or ""
    selected, hit_log = [], []
    chosen_ids = set()
    fresh_ids = set()  # 本轮真正命中（关键词/语义）的条目，用于刷新 warm 时间戳
    for e in rows:
        on = bool(e.get("always_on"))
        matched = [k for k in (e.get("keys") or []) if k and k in scan]
        if on or matched:
            e.pop("embedding", None)
            selected.append(e)
            chosen_ids.add(e.get("id"))
            if matched:
                fresh_ids.add(e.get("id"))
            if diag is not None:
                hit_log.append(
                    {
                        "title": e.get("title"),
                        "always_on": on,
                        "keys_hit": matched,
                        "via": "always" if on else "keyword",
                    }
                )

    # 语义通道：对尚未命中的条目按余弦排序，过阈值取 top-k（关键词已命中的不重复算）
    if query_vec is not None:
        scored = []
        for e in rows:
            if e.get("id") in chosen_ids:
                continue
            vec = _decode_vec(e.get("embedding"))
            if not vec:
                continue
            score = _cosine(query_vec, vec)
            if score >= sem_threshold:
                scored.append((score, e))
        scored.sort(key=lambda x: x[0], reverse=True)
        for score, e in scored[: max(0, sem_topk)]:
            e.pop("embedding", None)
            selected.append(e)
            chosen_ids.add(e.get("id"))
            fresh_ids.add(e.get("id"))
            if diag is not None:
                hit_log.append(
                    {
                        "title": e.get("title"),
                        "always_on": False,
                        "keys_hit": [],
                        "via": "semantic",
                        "score": round(score, 3),
                    }
                )

    # 迟滞防抖：刷新本轮命中的时间戳，再把仍在 warm 窗口内、本轮未命中的条目补回来
    if warm_rounds > 0 and session_id is not None:
        st = _LORE_WARM.setdefault(session_id, {"round": 0, "hit": {}})
        st["round"] += 1
        cur_round = st["round"]
        for rid in fresh_ids:
            st["hit"][rid] = cur_round
        rows_by_id = {e.get("id"): e for e in rows}
        for rid, last in list(st["hit"].items()):
            age = cur_round - last
            if age > warm_rounds:
                del st["hit"][rid]  # 过期退场
                continue
            if rid in chosen_ids:
                continue  # 本轮已命中，无需 warm 补
            e = rows_by_id.get(rid)
            if e is None:
                del st["hit"][rid]  # 条目已被删
                continue
            e.pop("embedding", None)
            selected.append(e)
            chosen_ids.add(rid)
            if diag is not None:
                hit_log.append(
                    {
                        "title": e.get("title"),
                        "always_on": bool(e.get("always_on")),
                        "keys_hit": [],
                        "via": "warm",
                        "age": age,
                    }
                )

    # 排序：常驻优先，再按 priority 降序，最后 id 升序保持稳定
    selected.sort(
        key=lambda e: (
            not e.get("always_on"),
            -int(e.get("priority") or 0),
            e.get("id") or 0,
        )
    )
    # 预算截断（按正文字符数粗略估算）
    if budget_chars is not None:
        out, used = [], 0
        for e in selected:
            c = len(e.get("content") or "")
            if used + c > budget_chars and not e.get("always_on"):
                continue
            out.append(e)
            used += c
        selected = out
    if diag is not None:
        diag["lore"] = hit_log
    return selected


def pick_spontaneous_lore(
    scope: str,
    *,
    scene_scan: str = "",
    query_vec: Optional[list[float]] = None,
    exclude_ids: Optional[set] = None,
    min_priority: int = 1,
    cooldown_sec: float = 86400.0,
    now: Optional[float] = None,
) -> Optional[dict]:
    """主动召回选择器：从「非常驻、够重要、最近没被主动提过」的条目里挑一条，供 AI 主动讲述。
    与 recall_lore 反着来——不依赖用户输入匹配，而是挑「该浮现却长期沉底」的设定/回忆。
    相关性优先级：关键词命中当前场景 > 语义贴近 > 休眠时长（都没相关也会捞最久没提的，让背景终能讲出）。
    返回单条 dict（不含 embedding）或 None。"""
    if _conn is None:
        return None
    now = now if now is not None else time.time()
    exclude_ids = exclude_ids or set()
    scan = scene_scan or ""
    _clause, _params = _scope_in_clause(scope)
    rows = [
        _lore_row_to_dict(r, keep_embedding=True)
        for r in _conn.execute(
            f"SELECT * FROM lore WHERE {_clause} AND always_on=0", _params
        ).fetchall()
    ]
    cands = []
    for e in rows:
        if e.get("id") in exclude_ids:
            continue
        if int(e.get("priority") or 0) < min_priority:
            continue
        if now - float(e.get("last_surfaced") or 0) < cooldown_sec:
            continue  # 还在冷却，避免反复讲同一段
        cands.append(e)
    if not cands:
        return None

    def _score(e):
        kw = 1 if any(k and k in scan for k in (e.get("keys") or [])) else 0
        sem = 0.0
        if query_vec is not None:
            vec = _decode_vec(e.get("embedding"))
            if vec:
                sem = _cosine(query_vec, vec)
        dormancy = now - float(e.get("last_surfaced") or 0)
        return (kw, round(sem, 3), dormancy)

    cands.sort(key=_score, reverse=True)
    best = cands[0]
    best.pop("embedding", None)
    return best


def mark_lore_surfaced(row_id, ts: Optional[float] = None) -> bool:
    """打主动召回冷却戳：记下这条 lore 刚被主动提起的时间。"""
    if _conn is None:
        return False
    ts = ts if ts is not None else time.time()
    with _lock:
        cur = _conn.execute("UPDATE lore SET last_surfaced=? WHERE id=?", (ts, row_id))
        _conn.commit()
        return cur.rowcount > 0


# ==================== 世界书容器（worldbooks） ====================


def worldbook_scope(book_id) -> str:
    """世界书 id → lore 表里的 scope 字符串。"""
    return f"wb:{book_id}"


def create_worldbook(name: str, bind_type: str = "none", bind_target: str = "") -> int:
    """新建一本世界书。bind_type ∈ {'character','user','none'}。返回 id。"""
    if bind_type not in ("character", "user", "none"):
        bind_type = "none"
    now = time.time()
    with _lock:
        cur = _conn.execute(
            "INSERT INTO worldbooks (name, bind_type, bind_target, created_at, updated_at) "
            "VALUES (?,?,?,?,?)",
            (name or "未命名世界书", bind_type, bind_target or "", now, now),
        )
        _conn.commit()
        return cur.lastrowid


def list_worldbooks() -> list[dict]:
    """列出所有世界书，附带各自的条目数（entry_count）。"""
    if _conn is None:
        return []
    rows = _conn.execute("SELECT * FROM worldbooks ORDER BY id ASC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        scope = worldbook_scope(d["id"])
        cnt = _conn.execute(
            "SELECT count(*) FROM lore WHERE scope=?", (scope,)
        ).fetchone()[0]
        d["entry_count"] = cnt
        out.append(d)
    return out


def get_worldbook(book_id) -> Optional[dict]:
    if _conn is None:
        return None
    r = _conn.execute("SELECT * FROM worldbooks WHERE id=?", (book_id,)).fetchone()
    return dict(r) if r else None


def update_worldbook(book_id, *, name=None, bind_type=None, bind_target=None) -> bool:
    sets, params = [], []
    if name is not None:
        sets.append("name=?")
        params.append(name)
    if bind_type is not None:
        if bind_type not in ("character", "user", "none"):
            bind_type = "none"
        sets.append("bind_type=?")
        params.append(bind_type)
    if bind_target is not None:
        sets.append("bind_target=?")
        params.append(bind_target)
    if not sets:
        return False
    sets.append("updated_at=?")
    params.append(time.time())
    params.append(book_id)
    with _lock:
        cur = _conn.execute(
            f"UPDATE worldbooks SET {', '.join(sets)} WHERE id=?", params
        )
        _conn.commit()
        return cur.rowcount > 0


def delete_worldbook(book_id) -> bool:
    """删除一本世界书，连带清掉它名下的全部条目。"""
    scope = worldbook_scope(book_id)
    with _lock:
        _conn.execute("DELETE FROM lore WHERE scope=?", (scope,))
        cur = _conn.execute("DELETE FROM worldbooks WHERE id=?", (book_id,))
        _conn.commit()
        return cur.rowcount > 0


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
            scope=test_scope,
            session_id=test_session,
            summary="跟许今闻在初夏的雨中初次相遇",
            type="相遇",
            weight="核心",
            importance=5,
            embedding=[1.0, 0.0, 0.0],
            scene_id="scene_1",
            time_label="Day 1·午后",
            place_label="言城高铁站",
        )
        # 事件二：正交向量
        upsert_event(
            scope=test_scope,
            session_id=test_session,
            summary="许今闻分享了他深藏于阁楼底部的秘密画室",
            type="揭示",
            weight="主线",
            importance=4,
            embedding=[0.0, 1.0, 0.0],
        )
        # 事件三：高重合度向量，带激烈情感冲突
        upsert_event(
            scope=test_scope,
            session_id=test_session,
            summary="因为过去的心结，与许今闻在走廊发生激烈争执",
            type="冲突",
            weight="转折",
            importance=3,
            embedding=[0.9, 0.1, 0.0],
        )

        # 2. 模拟注入对话细节切片
        add_chunk(
            test_scope,
            "雨下得很大，他主动把伞向我倾斜。",
            session_id=test_session,
            embedding=[1.0, 0.0, 0.0],
        )
        add_chunk(
            test_scope,
            "秘密画室里挂满了未干的向日葵油画。",
            session_id=test_session,
            embedding=[0.0, 1.0, 0.0],
        )

        # 3. 写入知识图谱硬事实（含核心状态钢印）
        upsert_fact(test_scope, "许今闻", "擅长", "古典油画", is_state=True)
        upsert_fact(test_scope, "许今闻", "厌恶", "下雨天")

        # 4. 写入高维统计大纲与近况
        upsert_summary(f"arc:{test_scope}", "历经生死考验、相互救赎的宿命契约者。")
        upsert_summary(
            f"session:{test_session}", "双方刚刚经历了一场情绪宣泄，空气里弥漫着沉默。"
        )

        # -------- 断言测试 1: 检验向量空间余弦重排准确度 --------
        target_vec = [1.0, 0.0, 0.0]
        vec_results = recall_events(test_scope, query_vec=target_vec, k=3)
        assert len(vec_results) > 0, "向量召回结果不应为空"
        assert "雨中初次相遇" in vec_results[0]["summary"], (
            "向量 [1,0,0] 匹配最精确的事件应排第一"
        )

        # -------- 断言测试 2: 检验向量丢失后的纯文本降级匹配功能 --------
        kw_results = recall_events(test_scope, query_vec=None, query_text="争执", k=3)
        assert len(kw_results) > 0, "文本检索结果不应为空"
        assert "激烈争执" in kw_results[0]["summary"], (
            "降级后带有关键词'争执'的项目应当优先重排在首位"
        )

        # -------- 断言测试 2.5: 场景时空字段落库 + retracted/is_state/prune --------
        vr0 = recall_events(test_scope, query_vec=[1.0, 0.0, 0.0], k=1)[0]
        assert (
            vr0.get("scene_id") == "scene_1" and vr0.get("place_label") == "言城高铁站"
        ), "事件应携带场景时空锚点"

        # retracted：推翻"厌恶下雨天"后应被物理删除
        upsert_fact(test_scope, "许今闻", "厌恶", retracted=True)
        preds = {(f["subject"], f["predicate"]) for f in get_facts(test_scope)}
        assert ("许今闻", "厌恶") not in preds, "retracted 事实应被删除"

        # prune：灌入大量非核心事实后裁剪，核心事实(擅长)必须幸存
        for i in range(90):
            upsert_fact(test_scope, "杂项", f"偏好{i}", f"值{i}")
        removed = prune_facts(test_scope, max_limit=80)
        assert removed > 0, "超限应触发裁剪"
        survivors = get_facts(test_scope)
        assert len(survivors) <= 80, "裁剪后总数应回落到上限内"
        assert any(
            f["subject"] == "许今闻" and f["predicate"] == "擅长" for f in survivors
        ), "核心 is_state 事实应免疫删除"

        # -------- 人工审查 3: 渲染打印双模上下文状态 --------
        print("=" * 20 + " 模式一: 正常向量召回注入 " + "=" * 20)
        context_with_vec = build_memory_context(
            test_scope, test_session, query_vec=target_vec
        )
        print(context_with_vec)

        print("\n" + "=" * 20 + " 模式二: 向量空缺降级检索注入 " + "=" * 20)
        context_degraded = build_memory_context(
            test_scope, test_session, query_vec=None, query_text="画室"
        )
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
