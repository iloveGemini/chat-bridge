# -*- coding: utf-8 -*-
"""聊天主路由：发送/清空/外部回复(claude_mode)/TTS/中断/编辑/删除/重roll。"""
import base64
import json
import threading
import time

from core.config import config, config_lock, save_config as _save_config
from core.net import _safe_decode, _safe_name, log_print
from core.paths import UPLOAD_DIR
from session.session import SESSION_BINDING_KEYS, global_pending_event
from prompts.prompts import _get_display_name
from memory.memory import _needs_summary, summarize_session
from chat.scene import _scene_stamp
from chat.envelope import ingest_reply
from chat.tts import _tts_cfg, synth_tts, _character_voice
from chat.notify import _push_notify
from chat.llm import call_llm_api
from routes.registry import post


@post("/api/submit")
def _submit(h, query, session, session_id):
    length = int(h.headers.get("Content-Length", 0))
    body = _safe_decode(h.rfile.read(length))
    try:
        data = json.loads(body)
    except Exception:
        data = {"text": body}

    text = data.get("text", "").strip()
    image_data = data.get("image")

    if not text and not image_data:
        h._json({"ok": False, "error": "empty input"})
        return

    if config.get("mode") != "api" and image_data:
        try:
            b64_str = image_data.split(",", 1)[-1]
            file_path = UPLOAD_DIR / f"{session_id}_up_{int(time.time())}.jpg"
            file_path.write_bytes(base64.b64decode(b64_str))
            text += f"\n\n[注：用户图片保存在：{file_path.absolute()}]"
        except Exception as e:
            log_print(f"[警告] 图片保存失败: {e}")

    # 前端选择不再单独写服务器：会话绑定（preset/world/user/character 名字）
    # 跟着这条消息一起送来，在这里随消息的安全写入一并持久化。
    cfg = data.get("config")
    if isinstance(cfg, dict):
        with session.lock:
            for k in SESSION_BINDING_KEYS:
                if k in cfg and cfg[k]:
                    session.active_prompts[k] = _safe_name(cfg[k]) or "default"
        session.save_active_prompts()

    # 用户消息继承当前场景坐标盖戳（用于总结时按 scene 分块）
    msg_to_save = {
        "role": "user",
        "text": text,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        **_scene_stamp(session),
    }
    if image_data:
        msg_to_save["image"] = image_data

    with session.lock:
        session.messages.append(msg_to_save)
        # 👇 修复：在这个绝对同步的环节立刻挂上锁，防止前端由于时间差读到 False
        session.is_typing = True
        session.typing_ts = time.time()
        # pending 是前端等待锁的唯一判据：从用户发送这一刻起就 pending，
        # 不管 api/claude_mode，直到真正有回复（或中断/清空）才解除，不依赖 is_typing 的 120s 超时。
        session.pending_event.set()
        session.interrupted = False
    session.save_active_prompts()  # (注意原有代码这里可能是 save_messages_async，保持你的原有调用即可)
    session.save_messages_async()
    clean_input = " ".join(text.split())
    log_print(f"📥 [用户输入][{session_id}]: {clean_input[:35]}...")

    if config.get("mode") == "api":
        threading.Thread(target=call_llm_api, args=(session_id,)).start()
    else:
        with session.lock:
            session.pending_text = text
        session.pending_event.set()
        global_pending_event.set()

    h._json({"ok": True})
    return


@post("/api/clear")
def _clear(h, query, session, session_id):
    with session.lock:
        session.messages = []
        session.pending_event.clear()
        session.pending_text = ""
        session.is_typing = False
        session.interrupted = True
    session.save_messages_async()


@post("/api/reply")
def _reply(h, query, session, session_id):
    length = int(h.headers.get("Content-Length", 0))
    body = _safe_decode(h.rfile.read(length))
    _rbody = json.loads(body) if "{" in body else {"text": body}

    with session.lock:
        if session.interrupted:
            log_print(
                f"🚫 [claude_mode 丢弃回复][{session_id}] 会话已被中断，丢弃此条回复。"
            )
            # 不要在这里设 session.interrupted = False，因为可能还有后续工具调用返回
            h._json({"ok": True})
            return

    reply_txt = (_rbody.get("text", "") or "").strip()
    is_proactive = bool(_rbody.get("proactive"))
    # claude_mode 回复同样走信封解析：推进场景闩锁，只落干净正文
    reply_txt, _meta = ingest_reply(session, reply_txt)
    msg = {
        "role": "assistant",
        "text": reply_txt,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        **_scene_stamp(session),
    }
    if is_proactive:
        msg["proactive"] = True
    if _meta.get("emotion"):
        msg["emotion"] = _meta["emotion"]
    if _meta.get("voice_text"):
        msg["voice_text"] = _meta["voice_text"]
    with session.lock:
        session.messages.append(msg)
        # 👇 修复：外部写入消息后，必须解除打字状态并清空等待事件
        session.is_typing = False
        session.pending_event.clear()
    session.save_messages_async()
    # 主动消息（调度唤醒角色组织的）→ 推送到手机
    if is_proactive:
        char_name = _get_display_name(
            "character", session.active_prompts.get("character"), "AI助手"
        )
        ok, detail = _push_notify(char_name, reply_txt)
        log_print(
            f"🔔 [主动·claude_mode→{session.session_id}] 推送 ok={ok} ({detail})"
        )
    # claude_mode 的回复不走 call_llm_api，总结触发器需在此补上
    if _needs_summary(session):
        threading.Thread(target=summarize_session, args=(session,)).start()
    h._json({"ok": True})


@post("/api/tts")
def _tts(h, query, session, session_id):
    # 按需语音合成：前端点了播放 / 自动读开着时才调到这里，只为真听的句子付费
    length = int(h.headers.get("Content-Length", 0))
    try:
        data = json.loads(_safe_decode(h.rfile.read(length)))
    except Exception:
        data = {}
    if not _tts_cfg().get("enabled"):
        h._json({"ok": False, "error": "tts_disabled"})
        return
    # 音色优先级：试听传入的 voice（整体覆盖）> 当前会话角色的 voice > 全局 config.tts
    voice_override = data.get("voice")
    if isinstance(voice_override, dict) and voice_override:
        override = dict(voice_override)  # 试听：整体覆盖
    else:
        char_name = session.active_prompts.get("character") if session else None
        override = dict(_character_voice(char_name)) if char_name else {}
        em = data.get("emotion")  # 该条消息的情绪叠加到角色音色上
        if em:
            override["emotion"] = em
    audio_url = synth_tts(data.get("text", ""), override=override)
    if audio_url:
        h._json({"ok": True, "audio": audio_url})
    else:
        h._json({"ok": False, "error": "synth_failed"})
    return


@post("/api/tts/option")
def _tts_option(h, query, session, session_id):
    # 语音布尔小开关（前端一键切换 skip_narration / autoplay / enabled），单键合并保存
    length = int(h.headers.get("Content-Length", 0))
    try:
        data = json.loads(_safe_decode(h.rfile.read(length)))
    except Exception:
        data = {}
    key = data.get("key")
    if key not in ("enabled", "skip_narration", "autoplay"):
        h._json({"ok": False, "error": "bad_key"})
        return
    with config_lock:
        config.setdefault("tts", {})
        config["tts"][key] = bool(data.get("value"))
        val = config["tts"][key]
    _save_config()
    h._json({"ok": True, "key": key, "value": val})
    return


@post("/api/done")
def _done(h, query, session, session_id):
    with session.lock:
        session.pending_event.clear()
    h._json({"ok": True})


@post("/api/interrupt")
def _interrupt(h, query, session, session_id):
    with session.lock:
        session.is_typing = False
        session.pending_event.clear()
        session.interrupted = True
    h._json({"ok": True})


@post("/api/typing")
def _typing(h, query, session, session_id):
    with session.lock:
        session.is_typing = True
        session.typing_ts = time.time()
    h._json({"ok": True})


@post("/api/edit")
def _edit(h, query, session, session_id):
    length = int(h.headers.get("Content-Length", 0))
    data = json.loads(_safe_decode(h.rfile.read(length)))
    idx, text = data.get("index"), data.get("text", "").strip()
    with session.lock:
        if idx is not None and 0 <= idx < len(session.messages):
            session.messages[idx]["text"] = text
            session.messages[idx]["edited"] = True
    session.save_messages_async()
    h._json({"ok": True})


@post("/api/delete")
def _delete(h, query, session, session_id):
    length = int(h.headers.get("Content-Length", 0))
    idx = json.loads(_safe_decode(h.rfile.read(length))).get("index")
    with session.lock:
        if idx is not None and 0 <= idx < len(session.messages):
            session.messages.pop(idx)
    session.save_messages_async()
    h._json({"ok": True})


@post("/api/reroll")
def _reroll(h, query, session, session_id):
    length = int(h.headers.get("Content-Length", 0))
    idx = json.loads(_safe_decode(h.rfile.read(length))).get("index")
    with session.lock:
        if (
            idx is not None
            and 0 <= idx < len(session.messages)
            and session.messages[idx]["role"] == "assistant"
        ):
            session.messages.pop(idx)
        session.interrupted = False
    session.save_messages_async()
    if config.get("mode") == "api":
        threading.Thread(target=call_llm_api, args=(session_id,)).start()
    else:
        user_text = ""
        with session.lock:
            for m in reversed(session.messages):
                if m["role"] == "user":
                    user_text = m["text"]
                    break
            session.pending_text = user_text
        if user_text:
            session.pending_event.set()
            global_pending_event.set()
    h._json({"ok": True})

