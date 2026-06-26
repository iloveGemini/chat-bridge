"""
Chat Bridge Server (Dual Engine + Long-Term Auto Memory + Vision + Multi-Session + Modular Prompts)
Usage: python server.py [port]
"""

import http.server
import json
import sys
import threading
import time
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
import scheduler

# API_REQUEST_TIMESTAMPS 迁至 chat.llm（聊天限流，仅其内部用）

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
import routes
from chat.notify import _detect_lan_ip, set_lan_base
from chat.outreach import (
    _fire_outreach,
    _get_last_user_ts,
    _parse_when,
)
from core.config import (
    _auth_cfg,
    _auth_enabled,
    _expected_token,
    config,
)
from core.config import (
    load_config as _load_config,
)
from core.net import _safe_decode, log_print
from core.paths import (
    ARCHIVE_DIR,
    DATA_DIR,
    JOBS_DB,
    MEMORY_DB,
    PORT,
    PRESETS_DIR,
    PROMPTS_DIR,
    ROOT,
    SESSIONS_DIR,
    UPLOAD_DIR,
)
from memory.memory import (
    _migrate_legacy_memory,
)

# LAN_BASE 已迁至 chat.notify（get_lan_base/set_lan_base）
# "预设" 只打包这三个分类
from prompts.prompts import PROMPT_CATEGORIES  # 分类常量已迁出
from session.session import (
    get_session,
    global_pending_event,
    sessions_map,
)
from session.tools import get_session_tools, set_session_tools

# 提示词模块大类（world 世界设定已废弃，由独立「世界书」体系替代）
# PROMPT_CATEGORIES/PRESET_CATEGORIES 由 prompts.prompts 提供

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


# _safe_resolve_path 迁至 chat.llm


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


# 会话级工具授权(get_session_tools/set_session_tools)迁至 session.tools（见顶部导入）


PROTECTED_PROMPT_NAMES = {"default"}


# 主动联系/外联(proactive+outreach)迁至 chat.outreach；推送/LAN 迁至 chat.notify（见顶部导入）


# call_llm_api 迁至 chat.llm（见顶部导入）


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

        if routes.dispatch_post(self, path, query, session, session_id):
            return

        # 聊天主路由(submit/clear/reply/tts/tts·option/done/interrupt/typing/edit/delete/reroll)
        # 已迁至 routes.chat_routes（分发表处理）
        # 提示词/预设/会话 POST 路由已迁至 routes.prompt_routes
        if path == "/api/outreach":
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
        # /api/agent/* 已迁至 routes.agent_routes（分发表处理）
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

        if routes.dispatch_get(self, path, query, session, session_id):
            return

        if path == "/api/messages":
            with session.lock:
                self._json(session.messages)
            return

        # GET /api/config 迁至 routes.config_routes

        if path == "/api/tools":
            # 读取本会话的工具授权状态，供前端开关初始化
            self._json({"ok": True, "tools": get_session_tools(session.session_id)})
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

        if path == "/api/outreach":
            # 列出当前会话的主动联系任务
            self._json({"jobs": scheduler.list_jobs(session.session_id)})
            return

        # ================= Code Agent（薄路由） =================
        # /api/agent/*、/api/logs、/api/fs/list 已迁至 routes.agent_routes（分发表处理）

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
    set_lan_base(f"http://{local_ip}:{PORT}")  # 推送图标相对路径据此补成绝对 URL

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
