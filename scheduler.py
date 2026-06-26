# -*- coding: utf-8 -*-
"""主动联系调度引擎：角色自己的「小本子」。
角色可在聊天里实时给自己排一个「之后主动找用户」的任务；调度线程到点触发。
存储独立于记忆库（data/jobs.db），纯函数 + 注入回调，便于离线测试。

job 字段：
  id, session_id, kind, when_spec, mode, content, intention, enabled, created_at, last_run, next_run
  kind     : once(一次性) / daily(每日定点) / interval(每隔N秒) / idle(久未联系)
  when_spec: once→触发的 epoch 秒；daily→"HH:MM"；interval→秒数；idle→空闲阈值秒数
  mode     : push(到点直接推固定 content) / wake(到点唤醒 AI，按 intention 当场组织内容)
"""

import sqlite3
import threading
import time
from datetime import datetime, timedelta
from typing import Callable, Optional

_conn: Optional[sqlite3.Connection] = None
_lock = threading.Lock()

KINDS = ("once", "daily", "interval", "idle")
MODES = ("push", "wake")


def init_db(db_path: str) -> None:
    global _conn
    with _lock:
        _conn = sqlite3.connect(db_path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL;")
        _conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id TEXT NOT NULL,
          kind TEXT NOT NULL,
          when_spec TEXT NOT NULL,
          mode TEXT NOT NULL,
          content TEXT DEFAULT '',
          intention TEXT DEFAULT '',
          enabled INTEGER DEFAULT 1,
          created_at REAL,
          last_run REAL,
          next_run REAL
        );
        """)
        _conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_session ON jobs(session_id);"
        )
        _conn.commit()


def _row(r) -> dict:
    return dict(r) if r else None


def compute_next_run(
    kind: str, when_spec: str, now: float, last_run: Optional[float] = None
) -> Optional[float]:
    """算出下一次触发的 epoch 秒。idle 返回 None（由 due 时按空闲判断）。"""
    if kind == "once":
        try:
            return float(when_spec)
        except (TypeError, ValueError):
            return None
    if kind == "interval":
        try:
            sec = float(when_spec)
        except (TypeError, ValueError):
            return None
        base = last_run if last_run else now
        return base + sec
    if kind == "daily":
        try:
            hh, mm = (when_spec or "").split(":")
            hh, mm = int(hh), int(mm)
        except Exception:
            return None
        dt = datetime.fromtimestamp(now)
        target = dt.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if target.timestamp() <= now:
            target = target + timedelta(days=1)
        return target.timestamp()
    return None  # idle


def add_job(
    session_id, kind, when_spec, mode, *, content="", intention="", now=None
) -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind 须为 {KINDS}")
    if mode not in MODES:
        raise ValueError(f"mode 须为 {MODES}")
    now = now or time.time()
    nxt = compute_next_run(kind, str(when_spec), now)
    with _lock:
        cur = _conn.execute(
            """
            INSERT INTO jobs (session_id, kind, when_spec, mode, content, intention,
                              enabled, created_at, last_run, next_run)
            VALUES (?,?,?,?,?,?,1,?,?,?)
        """,
            (
                session_id,
                kind,
                str(when_spec),
                mode,
                content,
                intention,
                now,
                None,
                nxt,
            ),
        )
        _conn.commit()
        return _row(
            _conn.execute("SELECT * FROM jobs WHERE id=?", (cur.lastrowid,)).fetchone()
        )


def list_jobs(session_id=None, only_enabled=False) -> list:
    q = "SELECT * FROM jobs"
    cond, params = [], []
    if session_id is not None:
        cond.append("session_id=?")
        params.append(session_id)
    if only_enabled:
        cond.append("enabled=1")
    if cond:
        q += " WHERE " + " AND ".join(cond)
    q += " ORDER BY (next_run IS NULL), next_run ASC, id ASC"
    return [_row(r) for r in _conn.execute(q, params).fetchall()]


def get_job(job_id) -> Optional[dict]:
    return _row(_conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())


def set_enabled(job_id, enabled: bool) -> bool:
    with _lock:
        cur = _conn.execute(
            "UPDATE jobs SET enabled=? WHERE id=?", (1 if enabled else 0, job_id)
        )
        _conn.commit()
        return cur.rowcount > 0


def delete_job(job_id) -> bool:
    with _lock:
        cur = _conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        _conn.commit()
        return cur.rowcount > 0


def _advance_after_fire(job: dict, now: float) -> None:
    """触发后推进：once 关闭；daily/interval 算下一次；idle 只记 last_run。"""
    kind = job["kind"]
    if kind == "once":
        _conn.execute(
            "UPDATE jobs SET last_run=?, enabled=0, next_run=NULL WHERE id=?",
            (now, job["id"]),
        )
    elif kind in ("daily", "interval"):
        nxt = compute_next_run(kind, job["when_spec"], now, last_run=now)
        _conn.execute(
            "UPDATE jobs SET last_run=?, next_run=? WHERE id=?", (now, nxt, job["id"])
        )
    else:  # idle
        _conn.execute("UPDATE jobs SET last_run=? WHERE id=?", (now, job["id"]))


def due_jobs(
    now: Optional[float] = None,
    get_last_user_ts: Optional[Callable[[str], Optional[float]]] = None,
) -> list:
    """返回本次到点的任务（已在库里推进好下次触发）。
    idle 任务需要 get_last_user_ts(session_id)->epoch 来判断空闲；
    规则：空闲超阈值、且自用户上次发言后还没触发过 → 触发一次（用户再说话即可复位）。"""
    now = now or time.time()
    fired = []
    with _lock:
        rows = [
            _row(r)
            for r in _conn.execute("SELECT * FROM jobs WHERE enabled=1").fetchall()
        ]
        for job in rows:
            hit = False
            if job["kind"] == "idle":
                try:
                    thresh = float(job["when_spec"])
                except (TypeError, ValueError):
                    continue
                lut = get_last_user_ts(job["session_id"]) if get_last_user_ts else None
                if (
                    lut
                    and (now - lut) >= thresh
                    and (not job["last_run"] or job["last_run"] < lut)
                ):
                    hit = True
            else:
                if job["next_run"] is not None and now >= job["next_run"]:
                    hit = True
            if hit:
                _advance_after_fire(job, now)
                fired.append(job)
        if fired:
            _conn.commit()
    return fired


def run_loop(
    on_fire: Callable[[dict], None],
    get_last_user_ts: Optional[Callable[[str], Optional[float]]] = None,
    interval: float = 60.0,
    stop_event: Optional[threading.Event] = None,
) -> None:
    """后台线程主体：每 interval 秒扫一次，对每个到点任务调 on_fire(job)。
    on_fire 内部异常不会中断循环。"""
    while not (stop_event and stop_event.is_set()):
        try:
            for job in due_jobs(get_last_user_ts=get_last_user_ts):
                try:
                    on_fire(job)
                except Exception:
                    pass
        except Exception:
            pass
        if stop_event:
            stop_event.wait(interval)
        else:
            time.sleep(interval)
