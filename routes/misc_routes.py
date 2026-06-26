# -*- coding: utf-8 -*-
"""杂项路由：主动联系(outreach)增删改查、会话工具授权、消息/状态/打字/长轮询。"""
import json
import time

import scheduler
from core.config import config
from core.net import _safe_decode
from chat.outreach import _parse_when
from session.session import global_pending_event, _find_pending_session
from session.tools import get_session_tools, set_session_tools
from routes.registry import post, get


@post("/api/outreach")
def _outreach_create(h, query, session, session_id):
    # 手动新建一个主动联系任务（前端面板用；与角色自助排程同一套底层）
    length = int(h.headers.get("Content-Length", 0))
    data = json.loads(_safe_decode(h.rfile.read(length)))
    kind = data.get("kind")
    mode = data.get("mode", "wake")
    if kind not in scheduler.KINDS or mode not in scheduler.MODES:
        h._json({"ok": False, "error": "kind/mode 不合法"})
    else:
        when_spec = _parse_when(kind, data.get("when"))
        job = scheduler.add_job(
            session.session_id,
            kind,
            when_spec,
            mode,
            content=data.get("content", ""),
            intention=data.get("intention", ""),
        )
        h._json({"ok": True, "job": job})


@post("/api/outreach/delete")
def _outreach_delete(h, query, session, session_id):
    length = int(h.headers.get("Content-Length", 0))
    data = json.loads(_safe_decode(h.rfile.read(length)))
    rid = data.get("id")
    h._json({"ok": scheduler.delete_job(rid) if rid else False})


@post("/api/outreach/toggle")
def _outreach_toggle(h, query, session, session_id):
    length = int(h.headers.get("Content-Length", 0))
    data = json.loads(_safe_decode(h.rfile.read(length)))
    rid = data.get("id")
    h._json(
        {
            "ok": scheduler.set_enabled(rid, bool(data.get("enabled")))
            if rid
            else False
        }
    )


@post("/api/tools")
def _tools_set(h, query, session, session_id):
    # 按会话窗口授权工具（outreach/web/coding）。body 传要改的项即可，增量合并。
    length = int(h.headers.get("Content-Length", 0))
    data = json.loads(_safe_decode(h.rfile.read(length))) if length else {}
    cfg = set_session_tools(session.session_id, data)
    h._json({"ok": True, "tools": cfg})


@get("/api/messages")
def _get_messages(h, query, session, session_id):
    with session.lock:
        h._json(session.messages)
    return


@get("/api/tools")
def _get_tools_get(h, query, session, session_id):
    # 读取本会话的工具授权状态，供前端开关初始化
    h._json({"ok": True, "tools": get_session_tools(session.session_id)})
    return


@get("/api/debug/last_prompt")
def _get_debug_last_prompt(h, query, session, session_id):
    if session.last_llm_payload:
        h._json(session.last_llm_payload)
    else:
        h._json({"error": "暂无记录"})
    return


@get("/api/wait_pending")
def _get_wait_pending(h, query, session, session_id):
    if config.get("mode") == "api":
        h._json({"pending": False})
        return
    # 🔥 真正阻塞等待，零消耗挂机（不再忙轮询）。跨所有会话感知，不锁死在单个 session_id 上，
    # 否则用户在非 default 会话发消息时，挂在原 session 上等待的轮询永远收不到通知。
    found = _find_pending_session()
    if not found:
        global_pending_event.wait(timeout=86400)
        global_pending_event.clear()
        found = _find_pending_session()
    if found:
        with found.lock:
            pro = getattr(found, "proactive_request", None)
            if pro:
                # 调度唤醒：不是用户发来的消息，而是请角色主动组织一条消息
                found.proactive_request = None  # 消费一次
                text = (
                    f"（系统·主动联系时机：现在请你以角色身份主动给用户发一条消息，"
                    f"对方此刻不在对话里。事由/心情：{pro}。直接说正文，自然简短，"
                    f"贴合当前关系与场景，不要解释这是主动消息。回复请照常用 <msg> 信封，"
                    f"并在 /api/reply 时附带 proactive:true。）"
                )
                h._json(
                    {
                        "pending": True,
                        "text": text,
                        "proactive": True,
                        "session_id": found.session_id,
                        "scene": {
                            "scene_id": found.current_scene_id,
                            "time": found.current_time,
                            "place": found.current_place,
                        },
                    }
                )
            else:
                h._json(
                    {
                        "pending": True,
                        "text": found.pending_text,
                        "session_id": found.session_id,
                        "scene": {
                            "scene_id": found.current_scene_id,
                            "time": found.current_time,
                            "place": found.current_place,
                        },
                    }
                )
    else:
        h._json({"pending": False})
    return


@get("/api/typing_status")
def _get_typing_status(h, query, session, session_id):
    with session.lock:
        if session.is_typing and (
            time.time() - session.typing_ts > 120
        ):  # 改为 120
            session.is_typing = False
            session.current_status = ""
        # pending 不受 120s 超时影响：只有真正回复/中断/清空才会解除，是前端解锁等待态的唯一依据
        h._json(
            {
                "typing": session.is_typing,
                "pending": session.pending_event.is_set(),
                "status": session.current_status,
            }
        )
    return
    return


@get("/api/status")
def _get_status(h, query, session, session_id):
    last_user_ts = last_any_ts = None
    with session.lock:
        for m in reversed(session.messages):
            if last_any_ts is None:
                last_any_ts = m.get("ts")
            if m["role"] == "user" and last_user_ts is None:
                last_user_ts = m.get("ts")
            if last_user_ts and last_any_ts:
                break

        if session.is_typing and (
            time.time() - session.typing_ts > 120
        ):  # 改为 120
            session.is_typing = False
            session.current_status = ""

        h._json(
            {
                "message_count": len(session.messages),
                "last_user_ts": last_user_ts,
                "last_any_ts": last_any_ts,
                "pending": session.pending_event.is_set(),
                "typing": session.is_typing,
                "status": session.current_status,
                "mode": config.get("mode", "api"),
            }
        )
    return
    return


@get("/api/outreach")
def _get_outreach_list(h, query, session, session_id):
    # 列出当前会话的主动联系任务
    h._json({"jobs": scheduler.list_jobs(session.session_id)})
    return

