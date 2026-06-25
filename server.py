"""
Chat Bridge Server (Dual Engine + Long-Term Auto Memory + Vision + Multi-Session + Modular Prompts)
Usage: python server.py [port]
"""

import base64
import hashlib
import http.server
import json
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import agent  # 独立的 Code Agent 模块（自驱工具循环 + 沙箱工作区 + 进度卡）
try:
    # 新的 5 阶段 Coding Orchestrator（agent.py 退化为其底层能力库）
    from agents.coding.orchestrator import run_coding_task as _run_coding_orchestrator
except Exception as _e:  # 导入失败不影响旧路径，网关会自动回退
    _run_coding_orchestrator = None
    print(f"[warn] Coding Orchestrator 未加载，回退到 agent.run_agent_turn: {_e}")
import memory_store
import notify
import scheduler
import tooling

API_REQUEST_TIMESTAMPS = []  # 用一个列表来记录最近请求的时间戳

# Windows 终端 GBK 编码兼容：强制 UTF-8 输出，避免 emoji 导致 UnicodeEncodeError
# Windows 终端 GBK 编码兼容：强制 UTF-8 输出，避免 emoji 导致 UnicodeEncodeError
if sys.platform == "win32":
    import io

    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )
    sys.stderr = io.TextIOWrapper(
        sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True
    )

# 路径常量 / 配置单例 / 网络工具 已抽离到 core/ 包，这里导入回本模块命名空间，
# 既让本文件内的裸名引用（PORT/config/log_print…）继续可用，也保持 server.xxx 的向后兼容。
from core.paths import (
    PORT, ROOT, DATA_DIR, SESSIONS_DIR, PROMPTS_DIR, PRESETS_DIR,
    ARCHIVE_DIR, UPLOAD_DIR, CONFIG_FILE, MEMORY_DB, JOBS_DB, TTS_DIR,
)
from core.net import log_print, _http_post_json, _safe_decode, _safe_name, _extract_json
from core.config import (
    config, config_lock,
    load_config as _load_config, save_config as _save_config,
    _auth_cfg, _auth_enabled, _auth_token_for, _expected_token,
)
from session.session import (
    ChatSession, get_session, sessions_map, global_pending_event,
    _session_scope, _resolve_session_worldbooks,
    GENESIS_SCENE, SESSION_BINDING_KEYS,
)
from chat.scene import _scene_stamp, build_scene_block
from chat.envelope import parse_msg_envelope, ingest_reply
from chat.tts import _tts_cfg, _strip_narration, synth_tts, _attach_tts, _character_voice
from prompts.prompts import (
    _read_prompt_content, _resolve_preset, _get_display_name, _apply_macros,
    build_header_prompt, build_tail_anchor,
)
from memory.memory import (
    _embed_cfg, _memory_cfg, embed_texts, embed_query, _lore_embedding,
    build_injected_memory, run_summary, summarize_session, _needs_summary,
    _summ_meta, _set_summ_meta, _summ_batch, _migrate_legacy_memory,
    SUMMARY_SYSTEM_PROMPT,
)

LAN_BASE = ""  # 运行时填入 http://<本机IP>:PORT，用于把相对图标路径补成绝对 URL（Bark 等推送图标用）

# "预设" 只打包这三个分类
PRESET_CATEGORIES = ["main", "style", "post"]

# 提示词模块大类（world 世界设定已废弃，由独立「世界书」体系替代）
PROMPT_CATEGORIES = ["main", "character", "user", "style", "post"]

# 全局配置 config/config_lock 由 core.config 提供；
# 会话域(ChatSession/get_session/sessions_map/global_pending_event/
# GENESIS_SCENE/SESSION_BINDING_KEYS)由 session.session 提供（均见文件顶部导入）

# 过滤掉不需要让 AI 看到的干扰文件夹/文件
IGNORE_NAMES = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".idea",
    ".vscode",
    "data",
    "icons",
    ".DS_Store",
    "CLAUDE.md",
    "MEMORY_SYSTEM.md",
    "config.json",
}


def get_project_tree(root_dir, max_depth=3):
    """动态生成项目目录树文本"""
    root_path = Path(root_dir)
    tree_lines = []

    def _build_tree(current_path, prefix="", depth=0):
        if depth > max_depth:
            return

        try:
            # 过滤隐藏文件和黑名单
            items = [
                x
                for x in current_path.iterdir()
                if not x.name.startswith(".") and x.name not in IGNORE_NAMES
            ]
            # 文件夹排前面，文件排后面
            items.sort(key=lambda x: (not x.is_dir(), x.name))
        except PermissionError:
            return

        for i, item in enumerate(items):
            is_last = i == len(items) - 1
            connector = "└── " if is_last else "├── "
            tree_lines.append(f"{prefix}{connector}{item.name}")

            if item.is_dir():
                extension = "    " if is_last else "│   "
                _build_tree(item, prefix + extension, depth + 1)

    _build_tree(root_path)
    return "\n".join(tree_lines)


def _safe_resolve_path(rel_path):
    """
    路径安全卫士：将相对路径转换为绝对路径，并严格限制在项目 ROOT 目录内。
    防止 AI 使用 '../../' 逃逸出项目文件夹。
    """
    target = (ROOT / str(rel_path)).resolve()
    # 检查解析后的目标路径是否以 ROOT 作为前缀
    if not str(target).startswith(str(ROOT.resolve())):
        raise ValueError(f"⚠️ 安全拦截：拒绝访问项目范围外的路径 ({rel_path})")
    return target


def _find_pending_session():
    for s in list(sessions_map.values()):
        if s.pending_event.is_set():
            return s
    return None


# log_print 已迁移至 core.net（见文件顶部导入）


# ================= 目录与配置初始化 =================
def _init_dirs():
    for d in [
        DATA_DIR,
        SESSIONS_DIR,
        PROMPTS_DIR,
        PRESETS_DIR,
        ARCHIVE_DIR,
        UPLOAD_DIR,
    ]:
        d.mkdir(parents=True, exist_ok=True)

    # 自动初始化提示词结构
    for cat in PROMPT_CATEGORIES:
        cat_dir = PROMPTS_DIR / cat
        cat_dir.mkdir(exist_ok=True)

    _ensure_defaults()


def _ensure_defaults():
    """确保 default 角色和预设始终存在（被删则重建）"""
    DEFAULTS = {
        "main": {"name": "默认", "content": "你是一个极简的私人聊天伴侣。"},
        "style": {
            "name": "默认",
            "content": "不要像个AI助手，用简短、自然、像朋友发微信一样的语气回复。如果用户用中文，你就用中文。",
        },
        "character": {
            "name": "AI 助手",
            "content": "你是一个通用 AI 助手，没有特定人设。真诚、简洁地回答用户的问题，像朋友聊天一样自然。",
            "avatar": "",
        },
        "user": {"name": "默认", "content": ""},
        "post": {"name": "默认", "content": ""},
    }
    for cat, data in DEFAULTS.items():
        fpath = PROMPTS_DIR / cat / "default.json"
        needs_write = False
        if not fpath.exists():
            needs_write = True
        else:
            try:
                existing = json.loads(fpath.read_text(encoding="utf-8"))
                if existing.get("name") != data["name"] or (
                    data["content"] and not existing.get("content")
                ):
                    needs_write = True
            except Exception:
                needs_write = True
        if needs_write:
            fpath.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            log_print(f"[初始化] 写入默认提示词: {cat}/default.json")

    preset_file = PRESETS_DIR / "default.json"
    if not preset_file.exists():
        preset_file.write_text(
            json.dumps(
                {"main": "default", "style": "default", "post": "default"},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        log_print("[初始化] 重建默认预设: _preset/default.json")


# _load_config / _save_config / _http_post_json / 鉴权助手(_auth_*) / _safe_decode
# 均已迁移至 core.config 与 core.net（见文件顶部导入）。


# _safe_name 迁至 core.net；提示词拼装引擎迁至 prompts.prompts（见顶部导入）


# ================= 会话对象管理 (多开核心) =================
# ChatSession / get_session / _session_scope / _resolve_session_worldbooks
# 已迁移至 session.session（见文件顶部导入）。


# ================= 统一记忆系统（存储 + 召回 + 增量总结） =================


# 嵌入/召回/build_injected_memory/_extract_json 已迁出 -> memory.memory & core.net（见顶部导入）


# 信封解析(parse_msg_envelope/ingest_reply)迁至 chat.envelope；TTS 迁至 chat.tts（见顶部导入）


# _scene_stamp / build_scene_block 迁至 chat.scene（见顶部导入）


# SUMMARY_SYSTEM_PROMPT / run_summary 已迁至 memory.memory（见顶部导入）


# 记忆迁移/批次总结(summarize_session 等)已迁至 memory.memory（见顶部导入）


# ====== 会话级工具授权（按会话窗口逐个授权，而非全局总开关）======
SESSION_TOOL_KEYS = ("outreach", "web", "coding")
# 默认值与前端开关一致：主动联系默认开，联网检索 / 本地项目操控默认关。
SESSION_TOOL_DEFAULTS = {"outreach": True, "web": False, "coding": False}


def get_session_tools(session_id):
    """读取本会话的工具授权状态；文件缺失或损坏时回落到默认。"""
    cfg = dict(SESSION_TOOL_DEFAULTS)
    try:
        f = get_session(session_id).dir / "tools.json"
        if f.exists():
            saved = json.loads(f.read_text(encoding="utf-8"))
            for k in SESSION_TOOL_KEYS:
                if k in saved:
                    cfg[k] = bool(saved[k])
    except Exception:
        pass
    return cfg


def set_session_tools(session_id, patch):
    """按会话窗口合并写入工具授权，返回写入后的完整状态。"""
    cfg = get_session_tools(session_id)
    for k in SESSION_TOOL_KEYS:
        if isinstance(patch, dict) and k in patch:
            cfg[k] = bool(patch[k])
    f = get_session(session_id).dir / "tools.json"
    f.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


PROTECTED_PROMPT_NAMES = {"default"}


# ================= 主动联系调度：触发与生成 =================
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
    header_prompt = _apply_macros(build_header_prompt(session), char_name, user_name)
    memory_str = _apply_macros(
        build_injected_memory(session, intention or ""), char_name, user_name
    )
    tail_anchor = _apply_macros(
        build_tail_anchor(session, memory_str), char_name, user_name
    )

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
        "temperature": 0.8,
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


def _detect_lan_ip():
    """可靠地拿到本机 LAN IP：用 UDP 连一下外网地址读本地 sockname（不真正发包）。
    比 gethostbyname(gethostname()) 稳，后者常返回 127.0.x.x。"""
    import socket

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "127.0.0.1"


def _resolved_notify_cfg():
    """返回 notify 配置；若 bark.icon 是相对路径（/icons/x.png），补成绝对 URL。
    base 优先用 config.notify.icon_base（可填 https 隧道地址），否则用运行时探测的 LAN_BASE。"""
    import copy

    cfg = copy.deepcopy(config.get("notify") or {})
    base = (cfg.get("icon_base") or "").strip().rstrip("/") or LAN_BASE
    b = cfg.get("bark") or {}
    icon = (b.get("icon") or "").strip()
    if icon.startswith("/") and base:
        b["icon"] = base + icon
        cfg["bark"] = b
    return cfg


def _push_notify(title, body):
    """统一推送入口：解析图标 URL 后发推。返回 (ok, detail)。"""
    return notify.send_notification(_resolved_notify_cfg(), title, body)


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


# ----- RP 角色自助排程（agentic）：聊天里角色可给自己排「之后主动找用户」-----
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


def call_llm_api(session_id):
    global LAST_API_REQUEST_TIME  # 声明使用全局变量

    session = get_session(session_id)
    _load_config()
    api_cfg = config.get("api", {})

    with session.lock:
        session.is_typing = True
        session.typing_ts = time.time()
        session.current_status = "正在思考..."
        session.pending_event.set()
        recent_rounds = _memory_cfg().get("recent_rounds", 10)
        recent_msgs = [
            m
            for m in session.messages
            if m.get("type") not in ("reasoning", "tool_call", "tool_result")
        ][-(recent_rounds * 2) :]
        last_user_text = next(
            (
                m.get("text", "")
                for m in reversed(session.messages)
                if m.get("role") == "user"
            ),
            "",
        )

        char_name = _get_display_name(
            "character", session.active_prompts.get("character"), "AI助手"
        )
        user_name = _get_display_name(
            "user", session.active_prompts.get("user"), "用户"
        )

    # 1. 顶部静态人设 (带宏替换)
    header_prompt = _apply_macros(build_header_prompt(session), char_name, user_name)

    # 2. 锁外检索动态记忆 (带宏替换)
    memory_str = _apply_macros(
        build_injected_memory(session, last_user_text), char_name, user_name
    )

    # 3. 生成尾部锚定块 (带宏替换)
    tail_anchor = _apply_macros(
        build_tail_anchor(session, memory_str), char_name, user_name
    )

    # 4. 极其优雅地拼装三明治上下文
    api_messages = [{"role": "system", "content": header_prompt}]

    for i, m in enumerate(recent_msgs):
        is_last_msg = i == len(recent_msgs) - 1
        role = m["role"]
        raw_text = m.get("text", "")

        if m.get("image"):
            content_nodes = [
                {"type": "text", "text": (raw_text or "请看这张图片。")},
                {"type": "image_url", "image_url": {"url": m["image"]}},
            ]
            if is_last_msg and tail_anchor:
                content_nodes[0]["text"] += tail_anchor
            api_messages.append({"role": role, "content": content_nodes})
        else:
            content = raw_text
            if is_last_msg and tail_anchor:
                content = f"{content}{tail_anchor}"

            api_messages.append({"role": role, "content": content})

    url = f"{api_cfg.get('base_url', '').rstrip('/')}/chat/completions"
    payload = {
        "model": api_cfg.get("model", "deepseek-chat"),
        "messages": api_messages,
        "temperature": 0.7,
    }

    session.last_llm_payload = {
        "url": url,
        "model": payload["model"],
        "messages": api_messages,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    sys_preview = " ".join(header_prompt.split())[:40]
    last_u_preview = " ".join(last_user_text.split())[:40]
    mem_lines_count = len(memory_str.splitlines()) if memory_str else 0

    log_print(
        f"↗️ [LLM 请求][{session_id}] ── {payload['model']} ({len(api_messages)}条上下文)"
    )
    log_print(f"   ├─ System: {sys_preview}...")
    if mem_lines_count > 0:
        log_print(f"   ├─ 记忆块: 实时注入了 {mem_lines_count} 行长时上下文")
    log_print(f"   └─ User  : {last_u_preview}...")

    tools_cfg = get_session_tools(session_id)
    session_tools = []
    if tools_cfg.get("outreach") and _outreach_enabled():
        session_tools += _outreach_tool_defs()
    if tools_cfg.get("coding"):
        session_tools += tooling.get_coding_tools()
    if session_tools:
        payload["tools"] = session_tools
        payload["tool_choice"] = "auto"
        log_print(
            f"🔑 [工具授权][{session_id}] 本会话挂载: "
            f"{'主动联系 ' if (tools_cfg.get('outreach') and _outreach_enabled()) else ''}"
            f"{'本地项目操控' if tools_cfg.get('coding') else ''}".strip()
        )

    def _post(pl):
        global API_REQUEST_TIMESTAMPS

        MAX_RPM = 5  # 你的 API 限制（一分钟 5 次）
        WINDOW_SIZE = 60.0  # 窗口时间 60 秒

        now = time.time()
        # 清理 60 秒以前的过期记录
        API_REQUEST_TIMESTAMPS = [
            ts for ts in API_REQUEST_TIMESTAMPS if now - ts < WINDOW_SIZE
        ]

        # 检查频率是否超标
        if len(API_REQUEST_TIMESTAMPS) >= MAX_RPM:
            oldest_ts = API_REQUEST_TIMESTAMPS[0]
            wait_time = WINDOW_SIZE - (now - oldest_ts)

            if wait_time > 0:
                log_print(
                    f"⏳ [滑动窗口保护] 60秒内已发 {MAX_RPM} 次请求，强制挂起等待 {wait_time:.1f} 秒..."
                )
                time.sleep(wait_time + 0.5)  # 额外给0.5秒冗余
                now = time.time()

        # 记录本次请求时间
        API_REQUEST_TIMESTAMPS.append(now)

        return _http_post_json(
            url,
            pl,
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_cfg.get('api_key')}",
            },
            timeout=120,  # 防止排队时连接超时
            tag=f"聊天·{session_id}",
        )

    try:
        raw_reply = ""
        for _round in range(99):
            if session.interrupted:
                log_print(f"🚫 [LLM 请求][{session_id}] 已被用户中断")
                return
            res_data = _post(payload)
            if session.interrupted:
                log_print(f"🚫 [LLM 请求][{session_id}] 已被用户中断")
                return
            choice = res_data["choices"][0]["message"]
            tcs = choice.get("tool_calls") or []

            # 👇 [修改 2]：在这里拦截并打印 AI 的思考过程（上帝视角）
            ai_thought = choice.get("reasoning_content")
            if not ai_thought:
                ai_thought_match = re.search(
                    r"<think>(.*?)</think>", choice.get("content") or "", re.DOTALL
                )
                if ai_thought_match:
                    ai_thought = ai_thought_match.group(1)

            if ai_thought:
                # print(
                #    f"\n🧠 [AI 思考][{session_id}]:\n{ai_thought.strip()}\n" + "-" * 40
                # )
                with session.lock:
                    session.messages.append(
                        {
                            "role": "assistant",
                            "type": "reasoning",
                            "text": ai_thought.strip(),
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            **_scene_stamp(session),
                        }
                    )
                session.save_messages_async()

            if not tcs:
                raw_reply = (choice.get("content") or "").strip()
                break

            api_messages.append(
                {
                    "role": "assistant",
                    "content": choice.get("content") or "",
                    "tool_calls": tcs,
                }
            )
            for tc in tcs:
                fn = tc.get("function", {}) or {}
                try:
                    a = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    a = {}
                fname = fn.get("name", "")

                with session.lock:
                    session.current_status = f"正在调用工具: {fname}..."
                    session.messages.append(
                        {
                            "role": "assistant",
                            "type": "tool_call",
                            "tool_name": fname,
                            "tool_args": a,
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            **_scene_stamp(session),
                        }
                    )
                session.save_messages_async()
                # 👇 [修改 3]：在这里打印详细的工具调用参数
                print(f"⚙️  [调度工具]: {fname}")
                if fname == "run_terminal_command":
                    print(f"   > 执行命令: {a.get('command')}")
                elif fname == "apply_file_edits":
                    print(f"   > 修改文件: {a.get('filepath')}")
                    for idx, edit in enumerate(a.get("edits", [])):
                        print(
                            f"     [{idx + 1}] 替换 {edit.get('start_line')}~{edit.get('end_line')} 行"
                        )
                        new_text_preview = (
                            edit.get("new_content", "").splitlines()[0][:50]
                            if edit.get("new_content")
                            else ""
                        )
                        print(f"         + {new_text_preview}...")
                elif fname == "batch_write_files":
                    for f in a.get("files", []):
                        # print(f"   > 写入文件: {f.get('filepath')}")
                        pass
                elif fname == "grep_files":
                    print(f"   > 搜索内容: '{a.get('pattern')}'")
                elif fname == "read_file_with_lines":
                    print(f"   > 读取文件: {a.get('filepath')}")

                # ==========================
                # 👇 原有的工具派发执行区域 👇
                # ==========================
                if fname in tooling.CODING_TOOL_NAMES:
                    context = {
                        "root_dir": ROOT,
                        "prompts_dir": PROMPTS_DIR,
                        "sessions_dir": SESSIONS_DIR,
                        "safe_resolve_cb": _safe_resolve_path,
                        "get_session_cb": get_session,
                        "memory_store": memory_store,
                        "embed_cb": _lore_embedding,
                    }
                    r = tooling.execute_tool(fname, a, context)
                else:
                    r = _exec_outreach_tool(fname, a, session_id)
                # ==========================
                # 👆 原有的工具派发执行区域 👆
                # ==========================

                with session.lock:
                    session.messages.append(
                        {
                            "role": "assistant",
                            "type": "tool_result",
                            "tool_name": fname,
                            "text": json.dumps(r, ensure_ascii=False),
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            **_scene_stamp(session),
                        }
                    )
                session.save_messages_async()
                # ==========================
                # 👆 原有的工具派发执行区域 👆
                # ==========================

                api_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id"),
                        "content": json.dumps(r, ensure_ascii=False),
                    }
                )
            log_print(
                f"🛠️ [角色工具][{session_id}] 执行 {len(tcs)} 个 "
                f"({', '.join((t.get('function') or {}).get('name', '?') for t in tcs)})"
            )

            with session.lock:
                session.current_status = "正在思考..."

        # 解析信封：推进场景闩锁，只把干净 <content> 落库/发前端
        reply_text, _meta = ingest_reply(session, raw_reply)

        a_msg = {
            "role": "assistant",
            "text": reply_text,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            **_scene_stamp(session),
        }
        if _meta.get("emotion"):
            a_msg["emotion"] = _meta["emotion"]
        if _meta.get("voice_text"):
            a_msg["voice_text"] = _meta["voice_text"]
        with session.lock:
            session.messages.append(a_msg)
            session.is_typing = False
            session.current_status = ""
            session.pending_event.clear()
        session.save_messages_async()
        log_print(f"\n{'=' * 15} 🤖 [AI 回复 -> Session: {session_id}] {'=' * 15}")
        log_print(raw_reply)
        log_print(f"{'=' * 55}\n")

        if _needs_summary(session):
            log_print(f"🧠 [记忆触发] 增量总结 -> Session: {session_id}")
            threading.Thread(target=summarize_session, args=(session,)).start()

    except Exception as e:
        log_print(f"[API] 发生错误: {e}")
        with session.lock:
            session.messages.append(
                {
                    "role": "assistant",
                    "text": f"⚠️ 系统提示：{str(e)}",
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
            )
            session.is_typing = False
            session.current_status = ""
            session.pending_event.clear()
        session.save_messages_async()


# ================= HTTP 服务 =================
class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    # 新版前端在 frontend/ 目录：根路径与 css/ js/ 静态资源从这里取，
    # 其余（/logo.png、/icons、API 等）仍走 ROOT。
    def translate_path(self, path):
        clean = path.split("?", 1)[0].split("#", 1)[0]
        if clean in ("/", "/index.html"):
            return str(ROOT / "frontend" / "index.html")
        if clean.startswith("/css/") or clean.startswith("/js/"):
            return str((ROOT / "frontend").joinpath(clean.lstrip("/")))
        return super().translate_path(path)

    # 这些路径不依赖/不应该触发 get_session() 的自动新建副作用
    NO_SESSION_POST_PATHS = {
        "/api/prompts/save",
        "/api/prompts/delete",
        "/api/sessions/create",
        "/api/sessions/delete",
        "/api/sessions/rename",
        "/api/sessions/clone",
        "/api/sessions/pin",
        "/api/presets/save",
        "/api/presets/delete",
        "/api/toggle_mode",
        "/api/config/save",
        "/api/test_models",  # ⭐️ 新增：保存配置与拉取模型的路由
        "/api/notify/test",
        "/api/tts/option",  # 语音小开关（skip_narration 等），无会话副作用
        "/api/prompts/set_default_user",  # 设置全局默认用户角色（仅写 config）
        "/api/worldbooks/create",
        "/api/worldbooks/update",
        "/api/worldbooks/delete",
        # Code Agent：均不绑角色会话
        "/api/agent/create",
        "/api/agent/send",
        "/api/agent/delete",
        "/api/agent/update",
        "/api/agent/interrupt",
        "/api/agent/enqueue",
        "/api/agent/context/add",
        "/api/agent/context/remove",
    }
    NO_SESSION_GET_PATHS = {
        "/api/sessions/list",
        "/api/presets/list",
        "/api/presets/get",
        "/api/config",  # ⭐️ 新增：读取全局配置的路由
        # Code Agent
        "/api/agent/tasks",
        "/api/agent/task",
        "/api/agent/turns",
        "/api/agent/last_prompt",
        "/api/agent/context",
        "/api/agent/files",
        "/api/logs",
        "/api/fs/list",
    }

    # 鉴权放行：登录接口本身不需要 token（否则没法登录）
    AUTH_PUBLIC_PATHS = {"/api/login"}

    def _client_is_local(self):
        """本机访问（claude_mode 长轮询 / 本地浏览器）免口令。"""
        ip = (self.client_address[0] if self.client_address else "") or ""
        return ip.startswith("127.") or ip in ("::1", "localhost", "::ffff:127.0.0.1")

    def _check_auth(self):
        """LAN 访问需带正确 token；未开启鉴权或本机访问直接放行。"""
        if not _auth_enabled():
            return True
        if self._client_is_local():
            return True
        tok = self.headers.get("X-Auth-Token", "")
        return bool(tok) and tok == _expected_token()

    def _auth_gate(self, path):
        """统一入口拦截：未通过鉴权的 /api 请求直接 401。返回 True 表示已拦截。"""
        if (
            path.startswith("/api/")
            and path not in self.AUTH_PUBLIC_PATHS
            and not self._check_auth()
        ):
            self._json({"ok": False, "error": "auth_required"}, code=401)
            return True
        return False

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        _load_config()

        # 登录接口：放行，校验口令后签发 token
        if path == "/api/login":
            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(_safe_decode(self.rfile.read(length)))
            except Exception:
                data = {}
            if not _auth_enabled():
                self._json({"ok": True, "token": "", "auth": False})
            elif data.get("password", "") == _auth_cfg().get("password", ""):
                self._json({"ok": True, "token": _expected_token(), "auth": True})
            else:
                self._json({"ok": False, "error": "bad_password"}, code=401)
            return

        if self._auth_gate(path):
            return

        session_id = query.get("session_id", ["default"])[0]
        session = (
            None if path in self.NO_SESSION_POST_PATHS else get_session(session_id)
        )

        if path == "/api/submit":
            length = int(self.headers.get("Content-Length", 0))
            body = _safe_decode(self.rfile.read(length))
            try:
                data = json.loads(body)
            except Exception:
                data = {"text": body}

            text = data.get("text", "").strip()
            image_data = data.get("image")

            if not text and not image_data:
                self._json({"ok": False, "error": "empty input"})
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

            self._json({"ok": True})
            return

        elif path == "/api/clear":
            with session.lock:
                session.messages = []
                session.pending_event.clear()
                session.pending_text = ""
                session.is_typing = False
                session.interrupted = True
            session.save_messages_async()
        elif path == "/api/reply":
            length = int(self.headers.get("Content-Length", 0))
            body = _safe_decode(self.rfile.read(length))
            _rbody = json.loads(body) if "{" in body else {"text": body}

            with session.lock:
                if session.interrupted:
                    log_print(
                        f"🚫 [claude_mode 丢弃回复][{session_id}] 会话已被中断，丢弃此条回复。"
                    )
                    # 不要在这里设 session.interrupted = False，因为可能还有后续工具调用返回
                    self._json({"ok": True})
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
            self._json({"ok": True})

        elif path == "/api/tts":
            # 按需语音合成：前端点了播放 / 自动读开着时才调到这里，只为真听的句子付费
            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(_safe_decode(self.rfile.read(length)))
            except Exception:
                data = {}
            if not _tts_cfg().get("enabled"):
                self._json({"ok": False, "error": "tts_disabled"})
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
                self._json({"ok": True, "audio": audio_url})
            else:
                self._json({"ok": False, "error": "synth_failed"})
            return

        elif path == "/api/tts/option":
            # 语音布尔小开关（前端一键切换 skip_narration / autoplay / enabled），单键合并保存
            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(_safe_decode(self.rfile.read(length)))
            except Exception:
                data = {}
            key = data.get("key")
            if key not in ("enabled", "skip_narration", "autoplay"):
                self._json({"ok": False, "error": "bad_key"})
                return
            with config_lock:
                config.setdefault("tts", {})
                config["tts"][key] = bool(data.get("value"))
                val = config["tts"][key]
            _save_config()
            self._json({"ok": True, "key": key, "value": val})
            return

        elif path == "/api/done":
            with session.lock:
                session.pending_event.clear()
            self._json({"ok": True})

        elif path == "/api/interrupt":
            with session.lock:
                session.is_typing = False
                session.pending_event.clear()
                session.interrupted = True
            self._json({"ok": True})
        elif path == "/api/typing":
            with session.lock:
                session.is_typing = True
                session.typing_ts = time.time()
            self._json({"ok": True})

        # ====== 这是补回来的旧接口支持 ======
        elif path == "/api/edit":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(_safe_decode(self.rfile.read(length)))
            idx, text = data.get("index"), data.get("text", "").strip()
            with session.lock:
                if idx is not None and 0 <= idx < len(session.messages):
                    session.messages[idx]["text"] = text
                    session.messages[idx]["edited"] = True
            session.save_messages_async()
            self._json({"ok": True})

        elif path == "/api/delete":
            length = int(self.headers.get("Content-Length", 0))
            idx = json.loads(_safe_decode(self.rfile.read(length))).get("index")
            with session.lock:
                if idx is not None and 0 <= idx < len(session.messages):
                    session.messages.pop(idx)
            session.save_messages_async()
            self._json({"ok": True})

        elif path == "/api/reroll":
            length = int(self.headers.get("Content-Length", 0))
            idx = json.loads(_safe_decode(self.rfile.read(length))).get("index")
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
            self._json({"ok": True})

        # ================= 新增：多模块提示词管理 =================
        elif path == "/api/prompts/save":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(_safe_decode(self.rfile.read(length)))
            cat = data.get("category")
            name = _safe_name(data.get("name"))
            display_name = data.get("display_name", "") or name
            content = data.get("content", "")
            if cat in PROMPT_CATEGORIES and name:
                file_data = {"name": display_name, "content": content}
                fpath = PROMPTS_DIR / cat / f"{name}.json"
                # 头像：角色与用户都支持。未传 avatar（旧前端/克隆）时保留原图，避免被清空。
                if cat in ("character", "user"):
                    if data.get("avatar") is not None:
                        file_data["avatar"] = data.get("avatar", "")
                    elif fpath.exists():
                        try:
                            old_av = json.loads(fpath.read_text(encoding="utf-8")).get(
                                "avatar"
                            )
                            if old_av:
                                file_data["avatar"] = old_av
                        except Exception:
                            pass
                if cat == "character":
                    if isinstance(data.get("voice"), dict):
                        file_data["voice"] = data.get("voice")
                    elif fpath.exists():
                        # 未传 voice（如克隆/旧前端）时保留原有音色，避免被覆盖丢失
                        try:
                            old = json.loads(fpath.read_text(encoding="utf-8"))
                            if isinstance(old.get("voice"), dict):
                                file_data["voice"] = old["voice"]
                        except Exception:
                            pass
                fpath.write_text(
                    json.dumps(file_data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                self._json({"ok": True})
            else:
                self._json({"ok": False, "error": "Invalid params"})

        elif path == "/api/prompts/delete":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(_safe_decode(self.rfile.read(length)))
            cat = data.get("category")
            name = _safe_name(data.get("name"))
            if name == "default":
                self._json({"ok": False, "error": "默认项不可删除"})
            elif cat in PROMPT_CATEGORIES and name:
                fpath = PROMPTS_DIR / cat / f"{name}.json"
                if fpath.exists():
                    fpath.unlink()
                self._json({"ok": True})
            else:
                self._json({"ok": False, "error": "Invalid params"})

        elif path == "/api/prompts/use":
            # 选择对哪个会话生效（不传 session_id 时即当前 query 里的 session，默认 "default"）
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(_safe_decode(self.rfile.read(length)))
            with session.lock:
                for k, v in data.items():
                    # 修复：改为 SESSION_BINDING_KEYS，使得 preset, world, user 均可直接保存
                    if k in SESSION_BINDING_KEYS:
                        session.active_prompts[k] = _safe_name(v) or "default"
            session.save_active_prompts()
            self._json({"ok": True})

        elif path == "/api/prompts/set_default_user":
            # 设置全局默认用户角色（“我”页主名片、新会话默认带入）
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(_safe_decode(self.rfile.read(length)))
            key = _safe_name(data.get("key")) or "default"
            with config_lock:
                config["default_user"] = key
            _save_config()
            self._json({"ok": True, "default_user": key})

        # ================= 新增：会话管理 =================
        elif path == "/api/sessions/create":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(_safe_decode(self.rfile.read(length)))
            character = _safe_name(data.get("character")) or "default"
            char_label = character
            cfile = PROMPTS_DIR / "character" / f"{character}.json"
            if cfile.exists():
                try:
                    char_label = json.loads(cfile.read_text(encoding="utf-8")).get(
                        "name", character
                    )
                except Exception:
                    pass
            char_label = _safe_name(char_label) or character
            existing_ids = [d.name for d in SESSIONS_DIR.iterdir() if d.is_dir()]
            n = 1
            while f"{char_label}_{n}" in existing_ids:
                n += 1
            new_id = f"{char_label}_{n}"
            new_session = get_session(new_id)
            new_session.active_prompts["character"] = character
            new_session.save_active_prompts()
            self._json({"ok": True, "session_id": new_id})

        elif path == "/api/sessions/delete":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(_safe_decode(self.rfile.read(length)))
            del_id = data.get("session_id")
            existing = [d.name for d in SESSIONS_DIR.iterdir() if d.is_dir()]
            if not del_id or del_id not in existing:
                self._json({"ok": False, "error": "session not found"})
            elif len(existing) <= 1:
                self._json({"ok": False, "error": "至少保留一个会话"})
            else:
                sessions_map.pop(del_id, None)
                shutil.rmtree(SESSIONS_DIR / del_id, ignore_errors=True)
                memory_store.delete_scope(
                    f"sess:{del_id}", del_id
                )  # 连带清掉该会话记忆
                self._json({"ok": True})

        elif path == "/api/sessions/rename":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(_safe_decode(self.rfile.read(length)))
            old_id = data.get("session_id")
            new_name = data.get("name", "").strip()
            if not old_id or not new_name:
                self._json({"ok": False, "error": "缺少参数"})
            else:
                new_id = _safe_name(new_name) or old_id
                old_dir = SESSIONS_DIR / old_id
                new_dir = SESSIONS_DIR / new_id
                if not old_dir.exists():
                    self._json({"ok": False, "error": "会话不存在"})
                elif new_dir.exists() and new_id != old_id:
                    self._json({"ok": False, "error": "名称已被占用"})
                elif new_id == old_id:
                    self._json({"ok": True, "session_id": old_id})
                else:
                    s = sessions_map.pop(old_id, None)
                    old_dir.rename(new_dir)
                    if s:
                        s.session_id = new_id
                        s.dir = new_dir
                        s.messages_file = new_dir / "messages.json"
                        s.memory_file = new_dir / "memory.json"
                        s.prompts_file = new_dir / "active_prompts.json"
                        sessions_map[new_id] = s
                    # 会话改名 → session_id 变了，记忆作用域随之迁移，避免记忆丢失
                    memory_store.migrate_scope(
                        f"sess:{old_id}", f"sess:{new_id}", old_id, new_id
                    )
                    self._json({"ok": True, "session_id": new_id})

        elif path == "/api/sessions/clone":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(_safe_decode(self.rfile.read(length)))
            src_id = data.get("session_id")
            src_dir = SESSIONS_DIR / src_id if src_id else None
            if not src_id or not src_dir or not src_dir.exists():
                self._json({"ok": False, "error": "会话不存在"})
            else:
                existing_ids = [d.name for d in SESSIONS_DIR.iterdir() if d.is_dir()]
                n = 1
                while f"{src_id}_copy{n}" in existing_ids:
                    n += 1
                new_id = f"{src_id}_copy{n}"
                shutil.copytree(src_dir, SESSIONS_DIR / new_id)
                # 克隆=独立快照：把来源会话的记忆 fork 一份给克隆体，之后互不影响
                memory_store.fork_scope(
                    f"sess:{src_id}", f"sess:{new_id}", src_id, new_id
                )
                self._json({"ok": True, "session_id": new_id})

        elif path == "/api/sessions/pin":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(_safe_decode(self.rfile.read(length)))
            sid = data.get("session_id")
            s_dir = SESSIONS_DIR / sid if sid else None
            if not sid or not s_dir or not s_dir.exists():
                self._json({"ok": False, "error": "会话不存在"})
            else:
                meta_file = s_dir / "meta.json"
                meta = {}
                if meta_file.exists():
                    try:
                        meta = json.loads(meta_file.read_text(encoding="utf-8"))
                    except Exception:
                        pass
                meta["pinned"] = not meta.get("pinned", False)
                meta_file.write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                self._json({"ok": True, "pinned": meta["pinned"]})

        # ================= 新增：预设管理 (main+style+post 打包) =================
        elif path == "/api/presets/save":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(_safe_decode(self.rfile.read(length)))
            name = _safe_name(data.get("name"))
            if not name:
                self._json({"ok": False, "error": "Invalid params"})
            else:
                preset = {
                    k: _safe_name(data.get(k)) or "default" for k in PRESET_CATEGORIES
                }
                (PRESETS_DIR / f"{name}.json").write_text(
                    json.dumps(preset, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                self._json({"ok": True})

        elif path == "/api/presets/delete":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(_safe_decode(self.rfile.read(length)))
            name = _safe_name(data.get("name"))
            if name == "default":
                self._json({"ok": False, "error": "默认预设不可删除"})
            elif name:
                fpath = PRESETS_DIR / f"{name}.json"
                if fpath.exists():
                    fpath.unlink()
                self._json({"ok": True})
            else:
                self._json({"ok": False, "error": "Invalid params"})

        elif path == "/api/presets/apply":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(_safe_decode(self.rfile.read(length)))
            name = _safe_name(data.get("name"))
            fpath = PRESETS_DIR / f"{name}.json"
            if name and fpath.exists():
                preset = json.loads(fpath.read_text(encoding="utf-8"))
                for k in PRESET_CATEGORIES:
                    session.active_prompts[k] = preset.get(k, "default")
                session.save_active_prompts()
                self._json({"ok": True})
            else:
                self._json({"ok": False, "error": "preset not found"})

        elif path == "/api/toggle_mode":
            new_mode = "claude_mode" if config.get("mode") == "api" else "api"
            config["mode"] = new_mode
            _save_config()
            self._json({"ok": True, "mode": new_mode})

        elif path == "/api/config/save":
            length = int(self.headers.get("Content-Length", 0))
            new_cfg = json.loads(_safe_decode(self.rfile.read(length)))
            with config_lock:
                config.update(new_cfg)  # 深度更新内存中的 config
            _save_config()
            self._json({"ok": True})
            return

        elif path == "/api/test_models":
            # ⭐️ 由 Python 后端代理向目标 API 发送请求，完美避开浏览器的跨域 CORS 限制
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(_safe_decode(self.rfile.read(length)))
            base_url = data.get("base_url", "").rstrip("/")
            api_key = data.get("api_key", "")

            if not base_url:
                self._json({"ok": False, "error": "请先填写 base_url"})
                return

            # 智能兼容：如果用户没写 /models，自动补齐标准 OpenAI 兼容的 /models 路径
            target_url = (
                f"{base_url}/models" if not base_url.endswith("/models") else base_url
            )
            try:
                req = urllib.request.Request(
                    target_url, headers={"Authorization": f"Bearer {api_key}"}
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    res_body = json.loads(resp.read().decode("utf-8"))
                    models = [m["id"] for m in res_body.get("data", []) if "id" in m]
                    self._json({"ok": True, "models": models})
            except Exception as e:
                self._json({"ok": False, "error": str(e)})
            return

        elif path == "/api/notify/test":
            # 单独跑通推送通道用：往配置的通道发一条测试通知，把成败原样回报前端
            _load_config()
            ok, detail = _push_notify(
                "Chat Bridge 测试推送", "如果你在手机上看到这条，推送通道就通了 ✅"
            )
            icon_url = (_resolved_notify_cfg().get("bark") or {}).get("icon") or "(无)"
            log_print(f"🔔 [推送测试] ok={ok} detail={detail}")
            log_print(
                f"🔔 [推送测试] 图标URL={icon_url} —— 用手机浏览器打开它，能看到图才说明手机够得到"
            )
            self._json({"ok": ok, "detail": detail, "icon": icon_url})

        elif path == "/api/memory/summarize":
            threading.Thread(
                target=summarize_session, args=(session,), kwargs={"full": True}
            ).start()
            self._json({"ok": True})

        elif path == "/api/memory/edit":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(_safe_decode(self.rfile.read(length)))
            table, rid = data.get("table"), data.get("id")
            ok = False
            if table == "events":
                emb = embed_query(data["summary"]) if data.get("summary") else None
                ok = memory_store.update_event(
                    rid,
                    summary=data.get("summary"),
                    type=data.get("type"),
                    weight=data.get("weight"),
                    importance=data.get("importance"),
                    embedding=emb,
                )
            elif table == "facts":
                ok = memory_store.update_fact(
                    rid,
                    subject=data.get("subject"),
                    predicate=data.get("predicate"),
                    obj=data.get("object"),
                    is_state=data.get("is_state"),
                )
            elif table == "summaries" and data.get("key"):
                memory_store.upsert_summary(data["key"], data.get("text", ""))
                ok = True
            self._json({"ok": ok})

        elif path == "/api/memory/forget":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(_safe_decode(self.rfile.read(length)))
            table, rid = data.get("table"), data.get("id")
            if table not in ("events", "chunks", "facts", "summaries"):
                self._json({"ok": False, "error": "invalid table"})
            else:
                self._json({"ok": memory_store.forget(table, rid)})

        elif path == "/api/lore":
            # 新建世界书条目（挂到指定世界书 book_id 下）
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(_safe_decode(self.rfile.read(length)))
            title = (data.get("title") or "").strip()
            content = (data.get("content") or "").strip()
            book_id = data.get("book_id")
            if book_id is None:
                self._json({"ok": False, "error": "book_id 必填"})
            elif not title or not content:
                self._json({"ok": False, "error": "title/content 必填"})
            else:
                rid = memory_store.add_lore(
                    memory_store.worldbook_scope(book_id),
                    title,
                    content,
                    keys=data.get("keys") or [],
                    priority=data.get("priority", 0),
                    always_on=bool(data.get("always_on")),
                    embedding=_lore_embedding(title, content),
                )
                self._json({"ok": True, "id": rid})

        # ---------- 世界书容器（worldbooks）管理 ----------
        elif path == "/api/worldbooks/create":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(_safe_decode(self.rfile.read(length)))
            name = (data.get("name") or "").strip() or "未命名世界书"
            bind_type = data.get("bind_type", "none")
            bind_target = data.get("bind_target", "") or ""
            bid = memory_store.create_worldbook(name, bind_type, bind_target)
            self._json({"ok": True, "id": bid})

        elif path == "/api/worldbooks/update":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(_safe_decode(self.rfile.read(length)))
            bid = data.get("id")
            if bid is None:
                self._json({"ok": False, "error": "id 必填"})
            else:
                ok = memory_store.update_worldbook(
                    bid,
                    name=data.get("name"),
                    bind_type=data.get("bind_type"),
                    bind_target=data.get("bind_target"),
                )
                self._json({"ok": ok})

        elif path == "/api/worldbooks/delete":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(_safe_decode(self.rfile.read(length)))
            bid = data.get("id")
            self._json(
                {"ok": memory_store.delete_worldbook(bid) if bid is not None else False}
            )

        elif path == "/api/worldbooks/session/set":
            # 设置本会话手动挂载的世界书 id 列表（角色/用户绑定的不在此列，会自动并入）
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(_safe_decode(self.rfile.read(length)))
            ids = data.get("ids")
            if not isinstance(ids, list):
                self._json({"ok": False, "error": "ids 须为数组"})
            else:
                with session.lock:
                    session.active_worldbooks = [int(i) for i in ids]
                session.save_worldbooks()
                self._json({"ok": True})

        elif path == "/api/lore/update":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(_safe_decode(self.rfile.read(length)))
            rid = data.get("id")
            if not rid:
                self._json({"ok": False, "error": "id 必填"})
            else:
                emb = (
                    _lore_embedding(data.get("title"), data.get("content"))
                    if (
                        data.get("title") is not None or data.get("content") is not None
                    )
                    else None
                )
                ok = memory_store.update_lore(
                    rid,
                    title=data.get("title"),
                    content=data.get("content"),
                    keys=data.get("keys"),
                    priority=data.get("priority"),
                    always_on=data.get("always_on"),
                    embedding=emb,
                )
                self._json({"ok": ok})

        elif path == "/api/lore/delete":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(_safe_decode(self.rfile.read(length)))
            rid = data.get("id")
            self._json({"ok": memory_store.delete_lore(rid) if rid else False})

        elif path == "/api/lore/reindex":
            # 给指定世界书下「还没有向量」的存量条目补 embedding（语义召回上线后回填用）
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(_safe_decode(self.rfile.read(length))) if length else {}
            book_id = data.get("book_id")
            scope = (
                memory_store.worldbook_scope(book_id)
                if book_id is not None
                else _session_scope(session)
            )
            done, skipped = 0, 0
            for e in memory_store.list_lore(scope):
                if e.get("has_embedding"):
                    skipped += 1
                    continue
                emb = _lore_embedding(e.get("title"), e.get("content"))
                if emb is None:
                    skipped += 1
                    continue
                memory_store.update_lore(e["id"], embedding=emb)
                done += 1
            self._json({"ok": True, "reindexed": done, "skipped": skipped})

        elif path == "/api/outreach":
            # 手动新建一个主动联系任务（前端面板用；与角色自助排程同一套底层）
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(_safe_decode(self.rfile.read(length)))
            kind = data.get("kind")
            mode = data.get("mode", "wake")
            if kind not in scheduler.KINDS or mode not in scheduler.MODES:
                self._json({"ok": False, "error": "kind/mode 不合法"})
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
                self._json({"ok": True, "job": job})

        elif path == "/api/outreach/delete":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(_safe_decode(self.rfile.read(length)))
            rid = data.get("id")
            self._json({"ok": scheduler.delete_job(rid) if rid else False})

        elif path == "/api/outreach/toggle":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(_safe_decode(self.rfile.read(length)))
            rid = data.get("id")
            self._json(
                {
                    "ok": scheduler.set_enabled(rid, bool(data.get("enabled")))
                    if rid
                    else False
                }
            )

        elif path == "/api/tools":
            # 按会话窗口授权工具（outreach/web/coding）。body 传要改的项即可，增量合并。
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(_safe_decode(self.rfile.read(length))) if length else {}
            cfg = set_session_tools(session.session_id, data)
            self._json({"ok": True, "tools": cfg})

        # ================= Code Agent（薄路由，业务全在 agent.py） =================
        elif path == "/api/agent/create":
            data = self._read_json()
            task = agent.create_task(
                data.get("title") or "新任务",
                data.get("goal") or "",
                seed_dir=data.get("seed_dir") or None,
                work_dir=data.get("work_dir") or None,
            )
            self._json({"ok": True, "task": task})

        elif path == "/api/agent/send":
            data = self._read_json()
            task_id = data.get("task_id")
            text = (data.get("text") or "").strip()
            if not task_id or not text:
                self._json({"ok": False, "error": "task_id/text 必填"})
                return
            if agent.is_running(task_id):
                self._json({"ok": False, "error": "该任务正在运行中"})
                return
            # 网关路由：默认走新的 5 阶段 Orchestrator；config.coding.orchestrator=false 可回退旧大循环。
            with config_lock:
                _use_orch = (config.get("coding", {}) or {}).get("orchestrator", True)
            if _use_orch and _run_coding_orchestrator is not None:
                _runner, _mode = _run_coding_orchestrator, "orchestrator"
            else:
                _runner, _mode = agent.run_agent_turn, "legacy"
            print(f"[agent/send] task={task_id} 路由 -> {_mode}")
            # 后台线程跑，HTTP 立即返回；前端轮询 /api/agent/turns 取进度
            threading.Thread(
                target=_runner, args=(task_id, text), daemon=True
            ).start()
            self._json({"ok": True, "mode": _mode})

        elif path == "/api/agent/delete":
            data = self._read_json()
            agent.delete_task(data.get("task_id"))
            self._json({"ok": True})

        elif path == "/api/agent/update":
            data = self._read_json()
            tid = data.pop("task_id", None)
            if tid:
                agent.update_task(tid, **data)
            self._json({"ok": True, "task": agent.get_task(tid) if tid else None})

        elif path == "/api/agent/interrupt":
            data = self._read_json()
            tid = data.get("task_id")
            if tid:
                agent.request_cancel(tid)
            self._json({"ok": True})

        elif path == "/api/agent/enqueue":
            data = self._read_json()
            tid = data.get("task_id")
            text = (data.get("text") or "").strip()
            if tid and text:
                agent.enqueue_message(tid, text)
                self._json({"ok": True})
            else:
                self._json({"ok": False, "error": "task_id/text 必填"})

        elif path == "/api/agent/context/add":
            data = self._read_json()
            self._json(agent.add_context(
                data.get("task_id"), data.get("filepath"), data.get("mode") or "outline"
            ))

        elif path == "/api/agent/context/remove":
            data = self._read_json()
            self._json(agent.remove_context(data.get("task_id"), data.get("filepath")))

        else:
            self._json({"ok": False, "error": "not found"}, 404)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if self._auth_gate(path):
            return

        session_id = query.get("session_id", ["default"])[0]
        session = None if path in self.NO_SESSION_GET_PATHS else get_session(session_id)

        if path == "/api/messages":
            with session.lock:
                self._json(session.messages)
            return

        if path == "/api/config":
            with config_lock:
                self._json(config)
            return

        if path == "/api/tools":
            # 读取本会话的工具授权状态，供前端开关初始化
            self._json({"ok": True, "tools": get_session_tools(session.session_id)})
            return

        if path == "/api/sessions/list":
            s_list = []
            char_cache = {}
            for d in SESSIONS_DIR.iterdir():
                if not d.is_dir():
                    continue
                s = get_session(d.name)
                char_name = s.active_prompts.get("character", "default")

                if char_name not in char_cache:
                    cfile = PROMPTS_DIR / "character" / f"{_safe_name(char_name)}.json"
                    if cfile.exists():
                        try:
                            cdata = json.loads(cfile.read_text(encoding="utf-8"))
                        except Exception:
                            cdata = {}
                    else:
                        cdata = {}
                    char_cache[char_name] = {
                        "name": cdata.get("name", char_name),
                        "avatar": cdata.get("avatar", ""),
                    }
                meta = char_cache[char_name]

                preview = ""
                if s.messages:
                    last = s.messages[-1]
                    preview = (
                        "[图片]" if last.get("image") else last.get("text", "")[:30]
                    )

                updated_at = (
                    s.messages_file.stat().st_mtime if s.messages_file.exists() else 0
                )

                pinned = False
                meta_file = d / "meta.json"
                if meta_file.exists():
                    try:
                        pinned = json.loads(meta_file.read_text(encoding="utf-8")).get(
                            "pinned", False
                        )
                    except Exception:
                        pass

                s_list.append(
                    {
                        "id": d.name,
                        "character": char_name,
                        "character_name": meta["name"],
                        "avatar": meta["avatar"],
                        "preview": preview,
                        "updated_at": updated_at,
                        "pinned": pinned,
                    }
                )
            s_list.sort(key=lambda x: (-x["pinned"], -x["updated_at"]))
            self._json({"sessions": s_list})
            return

        if path == "/api/presets/list":
            names = [f.stem for f in PRESETS_DIR.glob("*.json")]
            self._json({"presets": names})
            return

        if path == "/api/presets/get":
            name = _safe_name(query.get("name", [""])[0])
            fpath = PRESETS_DIR / f"{name}.json"
            if name and fpath.exists():
                self._json(
                    {"ok": True, "data": json.loads(fpath.read_text(encoding="utf-8"))}
                )
            else:
                self._json({"ok": False})
            return

        if path == "/api/prompts/list":
            with session.lock:
                session.load_active_prompts()  # 修复：每次拉取列表前，强制同步一次本地文件

            tree = {}
            for cat in PROMPT_CATEGORIES:
                cat_dir = PROMPTS_DIR / cat
                tree[cat] = [f.stem for f in cat_dir.glob("*.json")]

            # 补回被截断的角色解析与返回响应的逻辑
            char_list = []
            for f in (PROMPTS_DIR / "character").glob("*.json"):
                try:
                    cdata = json.loads(f.read_text(encoding="utf-8"))
                    char_list.append(
                        {
                            "key": f.stem,
                            "name": cdata.get("name", f.stem),
                            "avatar": cdata.get("avatar", ""),
                        }
                    )
                except Exception:
                    char_list.append({"key": f.stem, "name": f.stem, "avatar": ""})

            # 用户分身（“我”页主名片 + 平行分身）：解析 user 类别下的完整资料
            user_list = []
            for f in sorted((PROMPTS_DIR / "user").glob("*.json")):
                try:
                    udata = json.loads(f.read_text(encoding="utf-8"))
                    user_list.append(
                        {
                            "key": f.stem,
                            "name": udata.get("name", f.stem),
                            "avatar": udata.get("avatar", ""),
                            "content": udata.get("content", ""),
                        }
                    )
                except Exception:
                    user_list.append(
                        {"key": f.stem, "name": f.stem, "avatar": "", "content": ""}
                    )
            # 默认用户角色：优先 config.default_user，其次落到 'default'/首个
            default_user = config.get("default_user") or "default"
            user_keys = {u["key"] for u in user_list}
            if default_user not in user_keys:
                default_user = (
                    "default"
                    if "default" in user_keys
                    else (user_list[0]["key"] if user_list else "default")
                )
            # 主身份排最前，其余按 key
            user_list.sort(key=lambda u: (u["key"] != default_user, u["key"]))

            self._json(
                {
                    "tree": tree,
                    "active": session.active_prompts,
                    "characters": char_list,
                    "users": user_list,
                    "default_user": default_user,
                }
            )
            return

        if path == "/api/prompts/get":
            cat = query.get("category", [""])[0]
            name = _safe_name(query.get("name", [""])[0])
            if cat not in PROMPT_CATEGORIES or not name:
                self._json({"ok": False})
                return
            fpath = PROMPTS_DIR / cat / f"{name}.json"
            if fpath.exists():
                data = json.loads(fpath.read_text(encoding="utf-8"))
                self._json({"ok": True, "data": data})
            else:
                self._json({"ok": False})
            return

        if path == "/api/debug/last_prompt":
            if session.last_llm_payload:
                self._json(session.last_llm_payload)
            else:
                self._json({"error": "暂无记录"})
            return

        if path == "/api/wait_pending":
            if config.get("mode") == "api":
                self._json({"pending": False})
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
                        self._json(
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
                        self._json(
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
                self._json({"pending": False})
            return

        # ====== 这是补回来的打字状态专用接口 ======
        if path == "/api/typing_status":
            with session.lock:
                if session.is_typing and (
                    time.time() - session.typing_ts > 120
                ):  # 改为 120
                    session.is_typing = False
                    session.current_status = ""
                # pending 不受 120s 超时影响：只有真正回复/中断/清空才会解除，是前端解锁等待态的唯一依据
                self._json(
                    {
                        "typing": session.is_typing,
                        "pending": session.pending_event.is_set(),
                        "status": session.current_status,
                    }
                )
            return
            return

        if path == "/api/status":
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

                self._json(
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

        if path == "/api/memory/context":
            q = query.get("q", [""])[0]
            self._json({"context": build_injected_memory(session, q)})
            return

        if path == "/api/lore":
            # 列出某本世界书（book_id）下的全部条目
            book_id = query.get("book_id", [None])[0]
            if book_id is None:
                self._json({"lore": [], "error": "book_id 必填"})
            else:
                self._json(
                    {
                        "lore": memory_store.list_lore(
                            memory_store.worldbook_scope(book_id)
                        )
                    }
                )
            return

        if path == "/api/worldbooks/list":
            # 全局：列出所有世界书（含条目数）
            self._json({"worldbooks": memory_store.list_worldbooks()})
            return

        if path == "/api/worldbooks/session":
            # 本会话视角：自动并入（角色/用户绑定）的 + 手动挂载的 + 全部可选书
            char = session.active_prompts.get("character") or "default"
            user = session.active_prompts.get("user") or "default"
            manual = set(getattr(session, "active_worldbooks", []) or [])
            books = memory_store.list_worldbooks()
            auto, others = [], []
            for wb in books:
                bt, tgt = wb.get("bind_type"), wb.get("bind_target")
                if bt == "character" and tgt == char:
                    wb = {**wb, "auto_reason": "character"}
                    auto.append(wb)
                elif bt == "user" and tgt == user:
                    wb = {**wb, "auto_reason": "user"}
                    auto.append(wb)
                else:
                    others.append(wb)
            self._json(
                {
                    "character": char,
                    "user": user,
                    "auto": auto,
                    "others": others,
                    "manual_ids": sorted(manual),
                }
            )
            return

        if path == "/api/outreach":
            # 列出当前会话的主动联系任务
            self._json({"jobs": scheduler.list_jobs(session.session_id)})
            return

        if path == "/api/memory/search":
            q = query.get("q", [""])[0]
            try:
                k = int(query.get("k", ["5"])[0])
            except ValueError:
                k = 5
            scope = _session_scope(session)
            qv = embed_query(q) if q else None
            self._json(
                {
                    "events": memory_store.recall_events(
                        scope, query_vec=qv, query_text=q, k=k
                    ),
                    "chunks": memory_store.recall_chunks(
                        scope, query_vec=qv, query_text=q, k=k
                    ),
                }
            )
            return

        if path == "/api/memory/list":
            scope = _session_scope(session)
            kind = query.get("kind", [None])[0]
            self._json({"items": memory_store.list_memories(scope, kind)})
            return

        if path == "/api/memory/overview":
            scope = _session_scope(session)
            sid = session.session_id
            items = memory_store.list_memories(scope)
            events = [i for i in items if i.get("__table__") == "events"]
            facts = [i for i in items if i.get("__table__") == "facts"]
            chunks = [i for i in items if i.get("__table__") == "chunks"]
            meta = memory_store.get_meta(f"summ:{sid}") or {"boundary": 0}
            with session.lock:
                total = len(session.messages)
            self._json(
                {
                    "scope": scope,
                    "counts": {
                        "events": len(events),
                        "facts": len(facts),
                        "chunks": len(chunks),
                    },
                    "arc": memory_store.get_summary(f"arc:{scope}"),
                    "arc_key": f"arc:{scope}",
                    "session_summary": memory_store.get_summary(f"session:{sid}"),
                    "session_key": f"session:{sid}",
                    "events": events,
                    "facts": facts,
                    "meta": {
                        "boundary": meta.get("boundary", 0),
                        "total_messages": total,
                        "state": meta.get("state", "idle"),
                        "last_status": meta.get("last_status"),
                        "last_time": meta.get("last_time"),
                        "last_error": meta.get("last_error"),
                    },
                }
            )
            return

        # ================= Code Agent（薄路由） =================
        if path == "/api/agent/tasks":
            self._json({"ok": True, "tasks": agent.list_tasks()})
            return

        if path == "/api/agent/task":
            tid = query.get("id", [""])[0]
            task = agent.get_task(tid)
            if not task:
                self._json({"ok": False, "error": "not found"}, 404)
                return
            cp = agent.get_checkpoint(tid)
            self._json(
                {
                    "ok": True,
                    "task": task,
                    "checkpoint": cp["card"] if cp else None,
                    "running": agent.is_running(tid),
                    "tree": agent.workspace_tree(tid),
                }
            )
            return

        if path == "/api/agent/turns":
            tid = query.get("id", [""])[0]
            after = int(query.get("after", ["0"])[0] or 0)
            self._json(
                {
                    "ok": True,
                    "turns": agent.get_turns(tid, after),
                    "running": agent.is_running(tid),
                }
            )
            return

        if path == "/api/logs":
            after = int(query.get("after", ["0"])[0] or 0)
            logs = agent.get_debug(after)
            self._json(
                {"ok": True, "logs": logs, "seq": (logs[-1]["id"] if logs else after)}
            )
            return

        if path == "/api/agent/last_prompt":
            tid = query.get("id", [""])[0]
            self._json({"ok": True, "last_prompt": agent.get_last_prompt(tid)})
            return

        if path == "/api/agent/context":
            tid = query.get("id", [""])[0]
            self._json({"ok": True, "context": agent.list_context(tid)})
            return

        if path == "/api/agent/files":
            tid = query.get("id", [""])[0]
            self._json({"ok": True, "files": agent.list_workspace_files(tid)})
            return

        if path == "/api/fs/list":
            # 服务端目录浏览（给前端文件夹选择器用）。只返回目录，不返回文件内容。
            raw = query.get("path", [""])[0]
            try:
                import os

                if not raw:
                    if os.name == "nt":
                        import string

                        roots = [
                            f"{d}:\\"
                            for d in string.ascii_uppercase
                            if os.path.exists(f"{d}:\\")
                        ]
                        self._json(
                            {"ok": True, "path": "", "parent": None, "dirs": roots}
                        )
                    else:
                        base = Path("/")
                        dirs = []
                        for x in sorted(base.iterdir(), key=lambda z: z.name.lower()):
                            try:
                                if x.is_dir() and not x.name.startswith("."):
                                    dirs.append(str(x))
                            except (PermissionError, OSError):
                                pass
                        self._json(
                            {"ok": True, "path": "/", "parent": None, "dirs": dirs}
                        )
                    return
                pp = Path(raw)
                if not pp.exists() or not pp.is_dir():
                    self._json({"ok": False, "error": "目录不存在"})
                    return
                dirs = []
                for x in sorted(pp.iterdir(), key=lambda z: z.name.lower()):
                    try:
                        if x.is_dir() and not x.name.startswith("."):
                            dirs.append(str(x))
                    except (PermissionError, OSError):
                        pass
                parent = str(pp.parent) if str(pp.parent) != str(pp) else ""
                self._json(
                    {"ok": True, "path": str(pp), "parent": parent, "dirs": dirs}
                )
            except Exception as ex:
                self._json({"ok": False, "error": str(ex)})
            return

        super().do_GET()

    def _read_json(self):
        """读取并解析 POST body 为 dict，失败返回空 dict。"""
        try:
            length = int(self.headers.get("Content-Length", 0))
            if not length:
                return {}
            return json.loads(_safe_decode(self.rfile.read(length))) or {}
        except Exception:
            return {}

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")

        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")

        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    _init_dirs()
    _load_config()
    memory_store.init_db(str(MEMORY_DB))
    scheduler.init_db(str(JOBS_DB))
    agent.init_db()  # Code Agent 独立库 data/agent.db
    _migrate_legacy_memory()
    # 主动联系调度线程：每 60s 扫一次到点任务，触发 _fire_outreach
    threading.Thread(
        target=scheduler.run_loop,
        kwargs={
            "on_fire": _fire_outreach,
            "get_last_user_ts": _get_last_user_ts,
            "interval": 60,
        },
        daemon=True,
    ).start()
    log_print("⏰ [调度] 主动联系调度线程已启动（60s 轮询）")
    local_ip = _detect_lan_ip()
    LAN_BASE = f"http://{local_ip}:{PORT}"  # 推送图标相对路径据此补成绝对 URL

    print("\n" + "=" * 50)
    print(" \U0001f680 Chat Server (Multi-Session & Modular Prompt)")
    print(f" Current Mode -> {config.get('mode').upper()}")
    print(f" Web UI URL   -> http://{local_ip}:{PORT}")
    print(f" Sessions Dir -> {SESSIONS_DIR}")
    print(f" Prompts Dir  -> {PROMPTS_DIR}")
    print("=" * 50 + "\n")

    for d in SESSIONS_DIR.iterdir():
        if d.is_dir():
            get_session(d.name)
    get_session("default")
    log_print(f"\U0001f4c1 [本地缓存] 已静默预载 {len(sessions_map)} 个角色 Session")

    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log_print("正在关闭服务器...")
    finally:
        server.shutdown()
