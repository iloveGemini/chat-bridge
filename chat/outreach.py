# -*- coding: utf-8 -*-
"""主动联系：空闲判定 / 主动消息生成 / 调度回调 / RP 角色自助排程工具。"""
import time

from core.config import config, load_config as _load_config
from core.net import log_print, _http_post_json
from core.model_params import build_sampling
from prompts.prompts import _get_display_name, _apply_macros
from prompts.assembler import PromptAssembler
from memory.memory import build_injected_memory, _memory_cfg
from chat.envelope import ingest_reply
from chat.scene import _scene_stamp
from chat.notify import _push_notify
from session.session import get_session, global_pending_event
import scheduler


def _get_last_user_ts(session_id):
    """该会话最后一条用户消息的 epoch 秒（供 idle 任务判断空闲）。无则 None。"""
    try:
        s = get_session(session_id)
        with s.lock:
            msgs = list(s.messages)
        for m in reversed(msgs):
            if m.get("role") == "user" and m.get("ts"):
                try:
                    return time.mktime(time.strptime(m["ts"], "%Y-%m-%dT%H:%M:%S"))
                except Exception:
                    return None
    except Exception:
        return None
    return None


def _generate_proactive_message(session, intention):
    """api 模式唤醒：用角色人设+记忆当场生成一条主动消息正文。失败返回空串。"""
    _load_config()
    api_cfg = config.get("api", {})
    char_name = _get_display_name(
        "character", session.active_prompts.get("character"), "AI助手"
    )
    user_name = _get_display_name("user", session.active_prompts.get("user"), "用户")
    asm = PromptAssembler(session)
    _mem = build_injected_memory(session, intention or "")
    memory_before = _apply_macros(_mem["before"], char_name, user_name)
    memory_after = _apply_macros(_mem["after"], char_name, user_name)
    header_prompt = asm.build_system_head(char_name, user_name, memory_before)
    tail_anchor = asm.build_tail(char_name, user_name, memory_after)

    with session.lock:
        recent = [
            m
            for m in session.messages
            if m.get("type") not in ("reasoning", "tool_call", "tool_result")
        ][-(_memory_cfg().get("recent_rounds", 10) * 2) :]
    api_messages = [{"role": "system", "content": header_prompt}]
    for m in recent:
        if m.get("role") in ("user", "assistant"):
            api_messages.append({"role": m["role"], "content": m.get("text", "")})
    directive = (
        f"（系统提示：现在是你主动联系{user_name}的时机，对方此刻不在对话里。"
        f"你之前记下的心意/事由：{intention or '想找对方说说话'}。"
        f"请以{char_name}的身份，主动给{user_name}发一条自然、简短、贴合当前关系与场景的消息；"
        f"直接说正文，不要解释这是主动消息，不要寒暄式复述。）" + (tail_anchor or "")
    )
    api_messages.append({"role": "user", "content": directive})

    url = f"{api_cfg.get('base_url', '').rstrip('/')}/chat/completions"
    payload = {
        "model": api_cfg.get("model", "deepseek-chat"),
        "messages": api_messages,
        **build_sampling(api_cfg, 0.8),
    }
    try:
        res = _http_post_json(
            url,
            payload,
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_cfg.get('api_key')}",
            },
            timeout=90,
            tag="主动消息",
        )
        raw = res["choices"][0]["message"]["content"].strip()
        _content, _ = ingest_reply(session, raw)
        return _content
    except Exception as e:
        log_print(f"🔔 [主动消息生成失败] {e}")
        return ""


def _fire_outreach(job):
    """调度线程回调：一个到点任务的处理。push=直接推固定文案；wake=唤醒角色组织内容。"""
    sid = job.get("session_id")
    session = get_session(sid)
    char_name = _get_display_name(
        "character", session.active_prompts.get("character"), "AI助手"
    )
    mode = job.get("mode")
    text = ""

    if mode == "push":
        text = (job.get("content") or "").strip() or f"{char_name}想起了你。"
    else:  # wake
        if config.get("mode") == "api":
            text = _generate_proactive_message(session, job.get("intention"))
        else:
            # claude_mode：把主动请求挂到会话上，由 wait_pending 循环唤醒角色（我）来组织内容
            with session.lock:
                session.proactive_request = job.get("intention") or "想找对方说说话"
                session.pending_event.set()
            global_pending_event.set()
            log_print(f"🔔 [主动·claude_mode] 已唤醒会话 {sid} 让角色组织内容")
            return  # 内容由 claude 回复链路落库+推送，这里不重复

    if not text:
        return
    p_msg = {
        "role": "assistant",
        "text": text,
        "proactive": True,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        **_scene_stamp(session),
    }
    with session.lock:
        session.messages.append(p_msg)
        session.is_typing = False
        session.pending_event.clear()
    session.save_messages_async()
    ok, detail = _push_notify(char_name, text)
    log_print(f"🔔 [主动消息→{sid}] 已落库；推送 ok={ok} ({detail})")


def _outreach_enabled():
    return (config.get("outreach") or {}).get("enabled", True)


def _outreach_tool_defs():
    return [
        {
            "type": "function",
            "function": {
                "name": "schedule_outreach",
                "description": (
                    "当你（角色）产生了'之后想主动联系用户'的意愿时，用它给自己排一个提醒。"
                    "到点后系统会推送到用户手机。只在确有此意愿时调用，别滥用。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": ["once", "daily", "interval", "idle"],
                            "description": "once=某时刻一次 / daily=每天定点 / interval=每隔一段 / idle=用户久未说话时",
                        },
                        "when": {
                            "type": "string",
                            "description": (
                                "触发时机：once 用 'YYYY-MM-DD HH:MM' 或相对量 '+30m'/'+2h'/'+1d'；"
                                "daily 用 'HH:MM'；interval/idle 用分钟数（如 '180' 表示 3 小时）"
                            ),
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["wake", "push"],
                            "description": "wake=到点唤醒你当场组织内容(推荐) / push=直接推固定文案 content",
                        },
                        "intention": {
                            "type": "string",
                            "description": "wake 模式：你想说的事由/心情，到点据此生成消息",
                        },
                        "content": {
                            "type": "string",
                            "description": "push 模式：到点直接推送的固定文案",
                        },
                    },
                    "required": ["kind", "when", "mode"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_my_outreach",
                "description": "查看你给本会话排过的主动联系任务。",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "cancel_outreach",
                "description": "按 id 取消一个主动联系任务。",
                "parameters": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}},
                    "required": ["id"],
                },
            },
        },
    ]


def _parse_when(kind, when):
    now = time.time()
    w = str(when or "").strip()
    if kind == "daily":
        return w  # "HH:MM"
    if kind in ("interval", "idle"):
        try:
            return str(float(w) * 60)  # 分钟 → 秒
        except ValueError:
            return str(3600)
    # once
    if w.startswith("+") and len(w) >= 2 and w[-1] in "mhd":
        try:
            num = float(w[1:-1])
            return str(now + num * {"m": 60, "h": 3600, "d": 86400}[w[-1]])
        except ValueError:
            return str(now + 3600)
    try:
        return str(time.mktime(time.strptime(w, "%Y-%m-%d %H:%M")))
    except Exception:
        return str(now + 3600)


def _exec_outreach_tool(name, args, session_id):
    """RP 角色工具派发。硬边界：只能给本会话排/查/删主动联系任务，碰不到别的。"""
    try:
        if name == "schedule_outreach":
            kind, mode = args.get("kind"), args.get("mode", "wake")
            if kind not in scheduler.KINDS:
                return {"error": f"kind 须为 {scheduler.KINDS}"}
            if mode not in scheduler.MODES:
                return {"error": f"mode 须为 {scheduler.MODES}"}
            when_spec = _parse_when(kind, args.get("when"))
            job = scheduler.add_job(
                session_id,
                kind,
                when_spec,
                mode,
                content=args.get("content", ""),
                intention=args.get("intention", ""),
            )
            return {"ok": True, "id": job["id"], "next_run": job["next_run"]}
        if name == "list_my_outreach":
            return {"jobs": scheduler.list_jobs(session_id)}
        if name == "cancel_outreach":
            if not args.get("id"):
                return {"error": "id 必填"}
            return {"ok": scheduler.delete_job(args["id"])}
        return {"error": f"未知工具: {name}"}
    except Exception as e:
        return {"error": str(e)}
