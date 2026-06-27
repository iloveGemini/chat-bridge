# -*- coding: utf-8 -*-
"""DevTeam 持久化层（SQLite，按 task_id 隔离）。

DevTeam 是「专门开发本项目」的多角色团队 agent，与通用 coding 组并存。
它的元数据（事件 / 消息 / 状态快照 / 上下文缓存）全部进 SQLite，不落到被开发的仓库里：
  - devteam_events    状态/生命周期事件（唯一真相来源，支持 replay / rollback / diff）
  - devteam_messages  四层消息协议（Task/Report/Decision/Block）的交接信封
  - devteam_state     物化的当前状态快照（可由 events 重建）
  - devteam_context   Context Engineer 的 summaries / briefs（非状态，记忆/缓存）

三者刻意分离：Message ≠ State ≠ Event。
复用 coding_runtime 的同一个 agent.db 与连接/锁，task_id 与 tasks 表对齐。
"""
import json
import threading

import runtime.coding_runtime as cr

_init_lock = threading.Lock()
_initialized = False


def _ensure():
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        cr.init_db()  # 保证 tasks 等基础表 + 目录存在
        with cr._db_lock, cr._conn() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS devteam_events (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id     TEXT,
                    ts          TEXT,
                    event_type  TEXT,
                    actor       TEXT,
                    before_json TEXT,
                    after_json  TEXT,
                    reason      TEXT
                );
                CREATE TABLE IF NOT EXISTS devteam_messages (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id      TEXT,
                    ts           TEXT,
                    type         TEXT,
                    from_role    TEXT,
                    to_role      TEXT,
                    priority     TEXT,
                    confidence   INTEGER,
                    block_reason TEXT,
                    payload_json TEXT
                );
                CREATE TABLE IF NOT EXISTS devteam_state (
                    task_id       TEXT PRIMARY KEY,
                    snapshot_json TEXT,
                    updated_at    TEXT
                );
                CREATE TABLE IF NOT EXISTS devteam_context (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id   TEXT,
                    key       TEXT,
                    kind      TEXT,
                    content   TEXT,
                    ts        TEXT
                );
                CREATE TABLE IF NOT EXISTS devteam_kv (
                    task_id   TEXT,
                    key       TEXT,
                    value_json TEXT,
                    PRIMARY KEY (task_id, key)
                );
                CREATE INDEX IF NOT EXISTS idx_dt_events ON devteam_events(task_id, id);
                CREATE INDEX IF NOT EXISTS idx_dt_msgs   ON devteam_messages(task_id, id);
                CREATE INDEX IF NOT EXISTS idx_dt_ctx    ON devteam_context(task_id, id);
                """
            )
        _initialized = True


# ---------------- events（状态/生命周期事件，唯一真相来源） ----------------
def append_event(task_id, event_type, actor="", before=None, after=None, reason=""):
    """追加一条事件。before/after 存被改动时的完整状态快照（dev 规模可接受，
    换来 replay/rollback/diff 都极简、且不依赖物化快照表）。返回事件 id。"""
    _ensure()
    with cr._db_lock, cr._conn() as con:
        cur = con.execute(
            "INSERT INTO devteam_events(task_id,ts,event_type,actor,before_json,after_json,reason)"
            " VALUES(?,?,?,?,?,?,?)",
            (
                task_id,
                cr._now(),
                event_type,
                actor or "",
                json.dumps(before, ensure_ascii=False) if before is not None else None,
                json.dumps(after, ensure_ascii=False) if after is not None else None,
                reason or "",
            ),
        )
        return cur.lastrowid


def list_events(task_id, after_id=0):
    _ensure()
    with cr._conn() as con:
        rows = con.execute(
            "SELECT * FROM devteam_events WHERE task_id=? AND id>? ORDER BY id",
            (task_id, after_id),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["before"] = json.loads(d.pop("before_json")) if d.get("before_json") else None
        d["after"] = json.loads(d.pop("after_json")) if d.get("after_json") else None
        out.append(d)
    return out


def replay_state(task_id):
    """仅凭事件日志重建当前状态快照：STATE_CHANGED 事件的 after 即当时的完整状态，
    顺序回放后，最后一个 STATE_CHANGED 的 after 就是重建结果（不依赖 devteam_state 表）。"""
    state = None
    for ev in list_events(task_id):
        if ev.get("event_type") == "STATE_CHANGED" and ev.get("after") is not None:
            state = ev["after"]
    return state


# ---------------- state（物化快照，快读用；可由 events 重建） ----------------
def load_snapshot(task_id):
    _ensure()
    with cr._conn() as con:
        r = con.execute(
            "SELECT snapshot_json FROM devteam_state WHERE task_id=?", (task_id,)
        ).fetchone()
    if not r or not r["snapshot_json"]:
        return None
    try:
        return json.loads(r["snapshot_json"])
    except Exception:
        return None


def save_snapshot(task_id, snapshot):
    _ensure()
    payload = json.dumps(snapshot, ensure_ascii=False)
    with cr._db_lock, cr._conn() as con:
        con.execute(
            "INSERT INTO devteam_state(task_id,snapshot_json,updated_at) VALUES(?,?,?)"
            " ON CONFLICT(task_id) DO UPDATE SET snapshot_json=?,updated_at=?",
            (task_id, payload, cr._now(), payload, cr._now()),
        )


# ---------------- messages（四层协议交接信封） ----------------
def append_message(task_id, mtype, from_role="", to_role="", priority="normal",
                   confidence=None, block_reason="", payload=None):
    _ensure()
    with cr._db_lock, cr._conn() as con:
        cur = con.execute(
            "INSERT INTO devteam_messages(task_id,ts,type,from_role,to_role,priority,"
            "confidence,block_reason,payload_json) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                task_id, cr._now(), mtype, from_role or "", to_role or "",
                priority or "normal",
                int(confidence) if confidence is not None else None,
                block_reason or "",
                json.dumps(payload or {}, ensure_ascii=False),
            ),
        )
        return cur.lastrowid


def list_messages(task_id, after_id=0):
    _ensure()
    with cr._conn() as con:
        rows = con.execute(
            "SELECT * FROM devteam_messages WHERE task_id=? AND id>? ORDER BY id",
            (task_id, after_id),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d.pop("payload_json")) if d.get("payload_json") else {}
        out.append(d)
    return out


# ---------------- context（Context Engineer 产物，非状态） ----------------
def save_context(task_id, key, kind="brief", content=""):
    _ensure()
    with cr._db_lock, cr._conn() as con:
        con.execute(
            "INSERT INTO devteam_context(task_id,key,kind,content,ts) VALUES(?,?,?,?,?)",
            (task_id, key or "", kind or "brief", content or "", cr._now()),
        )
    return {"ok": True, "key": key, "kind": kind}


def list_context(task_id, after_id=0):
    _ensure()
    with cr._conn() as con:
        rows = con.execute(
            "SELECT * FROM devteam_context WHERE task_id=? AND id>? ORDER BY id",
            (task_id, after_id),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------- kv（轻量瞬态：Manager journal / 挂起的确认门等） ----------------
def get_kv(task_id, key, default=None):
    _ensure()
    with cr._conn() as con:
        r = con.execute(
            "SELECT value_json FROM devteam_kv WHERE task_id=? AND key=?", (task_id, key)
        ).fetchone()
    if not r or r["value_json"] is None:
        return default
    try:
        return json.loads(r["value_json"])
    except Exception:
        return default


def set_kv(task_id, key, value):
    _ensure()
    payload = json.dumps(value, ensure_ascii=False)
    with cr._db_lock, cr._conn() as con:
        con.execute(
            "INSERT INTO devteam_kv(task_id,key,value_json) VALUES(?,?,?)"
            " ON CONFLICT(task_id,key) DO UPDATE SET value_json=?",
            (task_id, key, payload, payload),
        )


def del_kv(task_id, key):
    _ensure()
    with cr._db_lock, cr._conn() as con:
        con.execute("DELETE FROM devteam_kv WHERE task_id=? AND key=?", (task_id, key))


def delete_all(task_id):
    """清空某任务的全部 devteam 元数据（删除任务时调用）。"""
    _ensure()
    with cr._db_lock, cr._conn() as con:
        for tbl in ("devteam_events", "devteam_messages", "devteam_state",
                    "devteam_context", "devteam_kv"):
            con.execute(f"DELETE FROM {tbl} WHERE task_id=?", (task_id,))
