"""
Chat Bridge Server (Dual Engine + Long-Term Auto Memory + Vision + Multi-Session + Modular Prompts)
Usage: python server.py [port]
"""
import http.server
import json
import re
import sys
import time
import shutil
import threading
import urllib.request
import urllib.error
import base64
import hashlib
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# Windows 终端 GBK 编码兼容：强制 UTF-8 输出，避免 emoji 导致 UnicodeEncodeError
# Windows 终端 GBK 编码兼容：强制 UTF-8 输出，避免 emoji 导致 UnicodeEncodeError
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)
import memory_store
import notify
import scheduler

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8800
ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
SESSIONS_DIR = DATA_DIR / "sessions"
PROMPTS_DIR = DATA_DIR / "prompts"
PRESETS_DIR = DATA_DIR / "prompts" / "_preset"
ARCHIVE_DIR = DATA_DIR / "archive"
UPLOAD_DIR = DATA_DIR / "uploads"
CONFIG_FILE = ROOT / "config.json"
MEMORY_DB = DATA_DIR / "memory.db"
JOBS_DB = DATA_DIR / "jobs.db"
LAN_BASE = ""  # 运行时填入 http://<本机IP>:PORT，用于把相对图标路径补成绝对 URL（Bark 等推送图标用）

# "预设" 只打包这三个分类
PRESET_CATEGORIES = ["main", "style", "post"]

# 提示词模块大类（world 世界设定已废弃，由独立「世界书」体系替代）
PROMPT_CATEGORIES = ["main", "character", "user", "style", "post"]

# 会话实际绑定的选择（只存名字）。preset 在生成时展开成 main/style/post。
SESSION_BINDING_KEYS = ["preset", "user", "character"]

# 场景闩锁创世初始值（新会话/无历史时的时空起点，由聊天 AI 在第 1 轮负责开辟时空）
GENESIS_SCENE = {"scene_id": "scene_0", "time": "未初始化", "place": "未初始化"}

# 全局配置和锁
config = {}
config_lock = threading.Lock()
sessions_map = {} # 内存中驻留的会话对象 { session_id: SessionObject }
global_pending_event = threading.Event() # claude_mode 下任意会话来新消息时触发，供 wait_pending 跨会话感知

def _find_pending_session():
    for s in list(sessions_map.values()):
        if s.pending_event.is_set():
            return s
    return None

def log_print(*args, **kwargs):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}]", *args, **kwargs)

# ================= 目录与配置初始化 =================
def _init_dirs():
    for d in [DATA_DIR, SESSIONS_DIR, PROMPTS_DIR, PRESETS_DIR, ARCHIVE_DIR, UPLOAD_DIR]:
        d.mkdir(parents=True, exist_ok=True)

    # 自动初始化提示词结构
    for cat in PROMPT_CATEGORIES:
        cat_dir = PROMPTS_DIR / cat
        cat_dir.mkdir(exist_ok=True)

    _ensure_defaults()

def _ensure_defaults():
    """确保 default 角色和预设始终存在（被删则重建）"""
    DEFAULTS = {
        "main":      {"name": "默认", "content": "你是一个极简的私人聊天伴侣。"},
        "style":     {"name": "默认", "content": "不要像个AI助手，用简短、自然、像朋友发微信一样的语气回复。如果用户用中文，你就用中文。"},
        "character": {"name": "AI 助手", "content": "你是一个通用 AI 助手，没有特定人设。真诚、简洁地回答用户的问题，像朋友聊天一样自然。", "avatar": ""},
        "user":      {"name": "默认", "content": ""},
        "post":      {"name": "默认", "content": ""},
    }
    for cat, data in DEFAULTS.items():
        fpath = PROMPTS_DIR / cat / "default.json"
        needs_write = False
        if not fpath.exists():
            needs_write = True
        else:
            try:
                existing = json.loads(fpath.read_text(encoding="utf-8"))
                if existing.get("name") != data["name"] or (data["content"] and not existing.get("content")):
                    needs_write = True
            except Exception:
                needs_write = True
        if needs_write:
            fpath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            log_print(f"[初始化] 写入默认提示词: {cat}/default.json")

    preset_file = PRESETS_DIR / "default.json"
    if not preset_file.exists():
        preset_file.write_text(json.dumps(
            {"main": "default", "style": "default", "post": "default"},
            ensure_ascii=False, indent=2
        ), encoding="utf-8")
        log_print("[初始化] 重建默认预设: _preset/default.json")

def _load_config():
    global config
    with config_lock:
        if CONFIG_FILE.exists():
            try:
                config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            except Exception as e:
                log_print(f"[警告] config.json 解析失败: {e}")

        # 补全缺省配置
        if "mode" not in config: config["mode"] = "api"
        if "api" not in config: config["api"] = {}
        config.setdefault("embedding", {"enabled": False, "base_url": "https://api.siliconflow.cn/v1", "api_key": "", "model": "BAAI/bge-m3"})
        config.setdefault("rerank", {"enabled": False, "base_url": "https://api.siliconflow.cn/v1", "api_key": "", "model": "BAAI/bge-reranker-v2-m3"})
        config.setdefault("summary_api", {"base_url": "", "api_key": "", "model": ""})
        config.setdefault("memory", {"recent_rounds": 10, "summarize_every": 16, "recall_n": 30, "top_k": 5, "recall_log": True})
        config.setdefault("auth", {"enabled": False, "password": ""})
        # 语音合成（MiniMax 预置音色 T2A）。base_url/api_key/group_id 留空即不生效，
        # 填上公益站或官方源就能给每条 AI 回复挂语音；voice_id 用 MiniMax 预置音色名。
        config.setdefault("tts", {
            "enabled": False,
            "base_url": "https://api.minimax.chat/v1",
            "api_key": "",
            "group_id": "",
            "model": "speech-01-turbo",
            "voice_id": "female-tianmei",
            "speed": 1.0, "vol": 1.0, "pitch": 0,
            "format": "mp3", "sample_rate": 32000,
            "autoplay": True,
            "skip_narration": False,  # True=只读台词、跳过（）【】*…*旁白与『』场景头
        })

def _save_config():
    with config_lock:
        CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def _http_post_json(url, payload, headers, timeout=90, max_retries=4, tag="LLM"):
    """POST JSON 并解析返回。对 429 / 5xx / 连接超时做指数退避重试（优先尊重 Retry-After 头），
    重试用尽仍失败则抛出最后一次异常，交调用方兜底。免费代理的瞬时限速不再一次就放弃。"""
    delay = 0.5
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_exc = e
            retryable = (e.code == 429) or (500 <= e.code < 600)
            if not retryable or attempt >= max_retries:
                raise
            wait = delay
            ra = e.headers.get("Retry-After") if e.headers else None
            if ra:
                try: wait = max(wait, float(ra))
                except Exception: pass
            log_print(f"⏳ [{tag}] {e.code} 限速/服务端忙，{wait:.1f}s 后重试（{attempt+1}/{max_retries}）")
            time.sleep(wait)
            delay = min(delay * 2, 8)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_exc = e
            if attempt >= max_retries:
                raise
            log_print(f"⏳ [{tag}] 连接异常（{e}），{delay:.1f}s 后重试（{attempt+1}/{max_retries}）")
            time.sleep(delay)
            delay = min(delay * 2, 8)
    if last_exc:
        raise last_exc

# ================= 局域网访问口令鉴权 =================
def _auth_cfg():
    return config.get("auth") or {}

def _auth_enabled():
    a = _auth_cfg()
    return bool(a.get("enabled") and a.get("password"))

def _auth_token_for(password):
    """口令 → 稳定 token（无状态，重启不失效）。token = sha256('chatbridge:'+口令)。"""
    return hashlib.sha256(("chatbridge:" + (password or "")).encode("utf-8")).hexdigest()

def _expected_token():
    return _auth_token_for(_auth_cfg().get("password", ""))

def _safe_decode(data):
    for enc in ("utf-8", "gbk"):
        try: return data.decode(enc)
        except Exception: continue
    return data.decode("utf-8", errors="replace")

def _safe_name(name):
    """只允许字母数字下划线短横线，防止路径穿越"""
    return "".join(c for c in (name or "") if c.isalnum() or c in "-_")

# ================= 提示词动态拼装引擎 =================
def _read_prompt_content(category, name):
    """按 分类+名字 读取某个提示词文件的正文内容；读不到返回空串。"""
    if not name:
        return ""
    p_file = PROMPTS_DIR / category / f"{_safe_name(name)}.json"
    if p_file.exists():
        try:
            return json.loads(p_file.read_text(encoding="utf-8")).get("content", "").strip()
        except Exception as e:
            log_print(f"[警告] 读取提示词文件 {p_file} 失败: {e}")
    return ""

def _resolve_preset(preset_name):
    """预设是 {main,style,post} 的引用包。返回 (main_name, style_name, post_name)。"""
    if not preset_name:
        return ("default", "default", "default")
    p_file = PRESETS_DIR / f"{_safe_name(preset_name)}.json"
    if p_file.exists():
        try:
            d = json.loads(p_file.read_text(encoding="utf-8"))
            return (d.get("main", "default"), d.get("style", "default"), d.get("post", "default"))
        except Exception as e:
            log_print(f"[警告] 读取预设 {p_file} 失败: {e}")
    return ("default", "default", "default")

def _get_display_name(category, file_name, default_val):
    """读取角色/用户设定的真实展示名称"""
    if not file_name or file_name == "default":
        return default_val
    p_file = PROMPTS_DIR / category / f"{_safe_name(file_name)}.json"
    if p_file.exists():
        try:
            return json.loads(p_file.read_text(encoding="utf-8")).get("name", file_name)
        except:
            pass
    return file_name

def _apply_macros(text, char_name, user_name):
    """替换全局宏变量 {{char}} 和 {{user}}"""
    if not text: return text
    return text.replace("{{char}}", char_name).replace("{{user}}", user_name)

def build_header_prompt(session):
    """
    【顶部磁铁】只放永恒不变的客观背景定义
    顺位：Role (Main/全局兜底) -> World Setting -> Persona -> Target User
    """
    _load_config()
    global_sys = config.get("api", {}).get("system_prompt", "").strip()
    active = session.active_prompts

    if "preset" in active and active.get("preset") not in ("", "default"):
        main_name, _, _ = _resolve_preset(active.get("preset"))
    else:
        main_name = active.get("main", "default")

    main_content = _read_prompt_content("main", main_name)
    if (not main_content or main_name == "default") and global_sys:
        main_content = global_sys

    parts = []
    if main_content:
        parts.append(f"<role_definition>\n{main_content}\n</role_definition>")

    c = _read_prompt_content("character", active.get("character", "default"))
    if c:
        parts.append(f"<persona>\n{c}\n</persona>")

    u = _read_prompt_content("user", active.get("user", "default"))
    if u:
        parts.append(f"<user_profile>\n{u}\n</user_profile>")

    return "\n\n".join(parts)


def build_tail_anchor(session, memory_str=""):
    """
    【尾部磁铁】动态驱动上下文与强制约束（定海神针）
    顺位：[召回的长时记忆 Memory] -> Dialogue Style -> Output Rules (Post)
    """
    active = session.active_prompts
    if "preset" in active and active.get("preset") not in ("", "default"):
        _, style_name, post_name = _resolve_preset(active.get("preset"))
    else:
        style_name = active.get("style", "default")
        post_name = active.get("post", "default")

    parts = []

    # 0. 当前场景状态 + 结构化输出/转场规约（动态，故放尾部而非静态 header）
    parts.append(build_scene_block(session))

    # 1. 召回的动态记忆
    if memory_str.strip():
        parts.append(f"<recalled_memory>\n{memory_str.strip()}\n</recalled_memory>")

    # 2. 说话文风
    s = _read_prompt_content("style", style_name)
    if s:
        parts.append(f"<dialogue_style>\n{s}\n</dialogue_style>")

    # 3. 压轴输出规约 (post)
    p = _read_prompt_content("post", post_name)
    if p:
        parts.append(f"<output_rules>\n{p}\n</output_rules>")

    compiled = "\n\n".join(parts)
    if compiled:
        # 用显式系统控制块包裹，防止 AI 误以为这些指令是普通用户打出来的字
        return f"\n\n\n<system_guidance>\n{compiled}\n</system_guidance>"
    return ""

# ================= 会话对象管理 (多开核心) =================
class ChatSession:
    def __init__(self, session_id):
        self.session_id = session_id
        self.dir = SESSIONS_DIR / session_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.messages_file = self.dir / "messages.json"
        self.memory_file = self.dir / "memory.json"
        self.prompts_file = self.dir / "active_prompts.json"
        self.worldbooks_file = self.dir / "worldbooks.json"

        self.lock = threading.Lock()
        self.messages = []
        self.pending_event = threading.Event()
        self.pending_text = ""
        self.proactive_request = None  # claude_mode 下被调度唤醒时，存待组织的主动联系事由
        self.is_typing = False
        self.typing_ts = 0
        # 会话绑定：只存名字。preset 默认 default，user/character 默认 default。
        self.active_prompts = {k: "default" for k in SESSION_BINDING_KEYS}
        # 本会话手动挂载的额外世界书 id 列表（角色/用户绑定的会自动并入，不存这里）
        self.active_worldbooks = []
        self.last_llm_payload = None

        # 场景闩锁：创世默认，随后从历史末条带戳消息恢复（重启不丢时空）
        self.current_scene_id = GENESIS_SCENE["scene_id"]
        self.current_time = GENESIS_SCENE["time"]
        self.current_place = GENESIS_SCENE["place"]

        self.load_messages()
        self.load_active_prompts()
        self.load_worldbooks()
        self._recover_scene_latch()

    def _recover_scene_latch(self):
        """从最近一条带场景戳的消息恢复时空坐标，避免重启被打回创世起点。"""
        for m in reversed(self.messages):
            if m.get("scene_id"):
                self.current_scene_id = m.get("scene_id") or self.current_scene_id
                self.current_time = m.get("time") or self.current_time
                self.current_place = m.get("place") or self.current_place
                break

    def load_messages(self):
        if self.messages_file.exists():
            try:
                self.messages = json.loads(self.messages_file.read_text(encoding="utf-8"))
            except Exception:
                self.messages = []

    def load_active_prompts(self):
        if self.prompts_file.exists():
            try:
                saved = json.loads(self.prompts_file.read_text(encoding="utf-8"))
                self.active_prompts.update(saved)
            except Exception:
                pass

    def _ensure_dir(self):
        # 缓存中的 session 对象可能比磁盘目录活得久（目录被删 / 同步软件移走 / 写入半途失败）。
        # 任何写入前都重新确保目录存在，否则 write_text 会抛 FileNotFoundError 直接让该请求返回空响应。
        self.dir.mkdir(parents=True, exist_ok=True)

    def save_active_prompts(self):
        with self.lock:
            self._ensure_dir()
            self.prompts_file.write_text(json.dumps(self.active_prompts, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_worldbooks(self):
        """读取本会话手动挂载的世界书 id 列表。"""
        if self.worldbooks_file.exists():
            try:
                saved = json.loads(self.worldbooks_file.read_text(encoding="utf-8"))
                if isinstance(saved, list):
                    self.active_worldbooks = [int(i) for i in saved]
            except Exception:
                pass

    def save_worldbooks(self):
        with self.lock:
            self._ensure_dir()
            self.worldbooks_file.write_text(
                json.dumps(self.active_worldbooks, ensure_ascii=False), encoding="utf-8")

    def save_messages_async(self):
        with self.lock:
            self._ensure_dir()
            # 💥 已物理超度上古时代的腰斩归档代码，让 messages.json 永久保存全量历史剧本
            tmp = self.messages_file.with_suffix('.tmp')
            tmp.write_text(json.dumps(self.messages, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.messages_file)

    def read_memory(self):
        if self.memory_file.exists():
            try:
                return json.loads(self.memory_file.read_text(encoding="utf-8")).get("profile", "")
            except Exception: pass
        return "暂时没有关于用户的特殊记忆。"

    def write_memory(self, profile_text):
        with self.lock:
            self._ensure_dir()
            data = {"profile": profile_text, "updated_at": time.strftime("%Y-%m-%d %H:%M")}
            self.memory_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def get_session(session_id="default"):
    if not session_id: session_id = "default"
    session_id = "".join([c for c in session_id if c.isalnum() or c in "-_"])
    if not session_id: session_id = "default"

    if session_id not in sessions_map:
        sessions_map[session_id] = ChatSession(session_id)
    else:
        # 缓存命中时也要确认磁盘目录还在，否则后续写入会抛 FileNotFoundError
        sessions_map[session_id]._ensure_dir()
    return sessions_map[session_id]

# ================= 统一记忆系统（存储 + 召回 + 增量总结） =================
def _session_scope(session):
    """记忆作用域：按会话隔离。新建会话=空记忆；克隆会话=fork 继承来源记忆。"""
    return f"sess:{session.session_id}"


def _resolve_session_worldbooks(session):
    """解析本会话适用的世界书：绑定到当前角色 + 绑定到当前用户 + 本会话手动挂载的。
    返回 (scope 列表, 命中的世界书 id 列表)。"""
    char = (session.active_prompts.get("character") or "default")
    user = (session.active_prompts.get("user") or "default")
    manual = set(getattr(session, "active_worldbooks", []) or [])
    ids = []
    for wb in memory_store.list_worldbooks():
        bid = wb["id"]
        bt, tgt = wb.get("bind_type"), wb.get("bind_target")
        if (bt == "character" and tgt == char) or \
           (bt == "user" and tgt == user) or \
           (bid in manual):
            ids.append(bid)
    return [memory_store.worldbook_scope(i) for i in ids], ids

def _embed_cfg(): return config.get("embedding", {}) or {}
def _memory_cfg(): return config.get("memory", {}) or {}

def embed_texts(texts):
    """线上 embedding（OpenAI 兼容 /embeddings）。未配置/失败返回 None → 触发关键词降级。"""
    cfg = _embed_cfg()
    if not cfg.get("enabled") or not cfg.get("api_key") or not cfg.get("base_url"):
        return None
    url = cfg["base_url"].rstrip("/") + "/embeddings"
    payload = {"model": cfg.get("model", "BAAI/bge-m3"), "input": texts}
    try:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {cfg.get('api_key')}"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return [item["embedding"] for item in data["data"]]
    except Exception as e:
        log_print(f"🧠 [embed 失败，降级关键词]: {e}")
        return None

def embed_query(text):
    if not text: return None
    vecs = embed_texts([text])
    return vecs[0] if vecs else None

def _lore_embedding(title, content):
    """世界书条目向量：标题+正文一起嵌入，供语义召回用。未配置 embedding 时返回 None。"""
    text = f"{title or ''} {content or ''}".strip()
    return embed_query(text) if text else None

def build_injected_memory(session, query_text):
    """组装注入 system prompt 的记忆块（facts+arc+近况+召回事件/切片），两模式共用。"""
    scope = _session_scope(session)
    qv = embed_query(query_text) if query_text else None
    mcfg = _memory_cfg()
    diag = {}
    # 世界书扫描文本：当前消息 + 当前场景的地点/时间（天然触发源）
    cur_t = getattr(session, "current_time", GENESIS_SCENE["time"])
    cur_p = getattr(session, "current_place", GENESIS_SCENE["place"])
    lore_scan = f"{query_text or ''} {cur_p} {cur_t}"
    lore_scopes, _ = _resolve_session_worldbooks(session)
    result = memory_store.build_memory_context(
        scope, session.session_id,
        query_vec=qv, query_text=query_text or "",
        top_k=mcfg.get("top_k", 5), recall_n=mcfg.get("recall_n", 30),
        lore_scan=lore_scan,
        lore_sem_topk=mcfg.get("lore_sem_topk", 3),
        lore_sem_threshold=mcfg.get("lore_sem_threshold", 0.40),
        lore_warm_rounds=mcfg.get("lore_warm_rounds", 2),
        lore_scopes=lore_scopes,
        diag=diag)
    # 主动召回（场景切换触发）：到了新场景，挑一条长期沉底的相关设定/回忆，
    # 以「邀请」而非「背景」的方式注入，让 AI 主动讲述（如童年往事），而不是等用户问。
    if mcfg.get("lore_spontaneous", True):
        last_scene = getattr(session, "_last_recall_scene_id", None)
        cur_scene = getattr(session, "current_scene_id", None)
        if last_scene is None:
            session._last_recall_scene_id = cur_scene   # 首次见到，仅初始化不触发
        elif cur_scene != last_scene:
            session._last_recall_scene_id = cur_scene
            try:
                ent = memory_store.pick_spontaneous_lore(
                    lore_scopes, scene_scan=f"{cur_p} {cur_t}", query_vec=qv,
                    min_priority=mcfg.get("lore_spont_min_priority", 1),
                    cooldown_sec=mcfg.get("lore_spont_cooldown_sec", 86400))
                if ent:
                    memory_store.mark_lore_surfaced(ent["id"])
                    spont = ("<spontaneous_recall>\n刚转入新场景，此刻很适合你主动、自然地提起下面这段"
                             "（觉得不贴合当下就跳过，别硬塞，也别每句都扯）：\n"
                             f"【{ent['title']}】{ent['content']}\n</spontaneous_recall>")
                    result = (result + "\n\n" + spont) if result else spont
                    log_print(f"💭 [主动召回·场景] 注入「{ent['title']}」@ {cur_p}")
            except Exception as e:
                log_print(f"💭 [主动召回失败]: {e}")

    # P0 召回可观测：每次召回打印一行（含模式/分数/命中/去重），方便肉眼判断准不准
    if mcfg.get("recall_log", True):
        try:
            log_print(memory_store.format_recall_log(diag))
        except Exception as e:
            log_print(f"🧠 [recall_log 失败]: {e}")
    return result

def _extract_json(text):
    """从 LLM 输出里抠出第一个 JSON 对象（容忍 ``` 围栏和前后废话）。"""
    if not text: return None
    import re
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m: t = m.group(1).strip()
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1 or end < start: return None
    try:
        return json.loads(t[start:end + 1])
    except Exception:
        return None

# ================= 结构化输出信封：解析 + 场景闩锁 =================
# AI 每轮回复用 <msg> 信封包裹：可选 <scene/> 元数据 + <content> 正文。
# 系统解析后只把 <content> 发前端 / 落库，<scene> 驱动后台时空状态机。
# 容错铁律：信封缺失或标签破损时，绝不报错——剥掉已知标签后整段当正文，宁可丢一次结构化也不让用户收到空消息。

def parse_msg_envelope(raw):
    """解析 <msg> 信封。返回 (clean_content:str, scene:dict|None, meta:dict)。
    scene 含 scene_id/time/place（缺项为 None）。
    meta = {emotion, voice_text}：emotion 取自 <content emotion="...">；
    voice_text 保留 <#x#> 停顿标记供 TTS，clean_content 抹掉这些标记供展示。
    解析失败时正文回退原文。"""
    if not raw:
        return "", None, {}
    text = raw.strip()
    scene = None
    meta = {}

    # 1. 抽场景元数据（自闭合 <scene .../>，属性顺序无关，容忍多空格/无斜杠）
    m = re.search(r'<scene\b([^>]*?)/?>', text, re.I)
    if m:
        attrs = m.group(1)
        def _attr(name):
            am = re.search(name + r'\s*=\s*"([^"]*)"', attrs, re.I)
            return am.group(1).strip() if am else None
        sid, t, p = _attr('id'), _attr('time'), _attr('place')
        if sid or t or p:
            scene = {"scene_id": sid, "time": t, "place": p}
        text = (text[:m.start()] + text[m.end():]).strip()  # 物理超度 scene 标签

    # 2. 抽正文：优先 <content emotion="...">…</content>
    cm = re.search(r'<content\b([^>]*)>(.*?)</content>', text, re.I | re.S)
    if cm:
        em = re.search(r'emotion\s*=\s*"([^"]*)"', cm.group(1), re.I)
        if em and em.group(1).strip():
            meta["emotion"] = em.group(1).strip()
        inner = cm.group(2).strip()
    else:
        # 兜底：剥掉残留的 <msg>/<content> 标签，剩下的整段当正文
        inner = re.sub(r'</?(?:msg|content)\b[^>]*>', '', text, flags=re.I).strip()

    # 3. 停顿标记 <#x#>：保留给语音，抹掉给展示
    display = re.sub(r'<#[^>]*?#>', '', inner)
    display = re.sub(r'[ \t]{2,}', ' ', display).strip()
    if inner != display:
        meta["voice_text"] = inner
    return display, scene, meta


def ingest_reply(session, raw_reply):
    """解析 AI 原始输出，推进场景闩锁，返回 (干净正文, meta)。两模式共用。
    meta 含可选 emotion / voice_text，供落库后给 TTS 用。"""
    content, scene, meta = parse_msg_envelope(raw_reply)
    if scene:
        with session.lock:
            if scene.get("scene_id"): session.current_scene_id = scene["scene_id"]
            if scene.get("time"):     session.current_time = scene["time"]
            if scene.get("place"):    session.current_place = scene["place"]
        log_print(f"🎬 [场景转换] -> {session.current_scene_id} ({session.current_time} @ {session.current_place})")
    # 兜底：正文为空（极端：只发了 scene 标签）时退回原文，绝不给前端空消息
    display = content if content else (raw_reply or "").strip()
    return display, meta


# ================= 语音合成（MiniMax 预置音色 T2A 旁路） =================
TTS_DIR = DATA_DIR / "tts"

def _tts_cfg():
    return config.get("tts", {}) or {}

def _strip_narration(text):
    """只读台词：去掉旁白——成对（中/英）括号、【】、*…* 包裹，以及整行的 『…』场景/时间头。
    保留 <#x#> 停顿标记不动。去完为空说明本条没台词。"""
    t = text or ""
    t = re.sub(r'（[^）]*）', '', t)        # 全角括号旁白
    t = re.sub(r'\([^)]*\)', '', t)         # 半角括号旁白
    t = re.sub(r'【[^】]*】', '', t)         # 方头括号旁白
    t = re.sub(r'\*[^*\n]+\*', '', t)       # *星号* 旁白
    t = re.sub(r'(?m)^\s*『[^』]*』\s*$', '', t)  # 整行场景/时间头
    t = re.sub(r'[ \t]{2,}', ' ', t)
    t = re.sub(r'\n{2,}', '\n', t)
    return t.strip()

def synth_tts(text, override=None):
    """把一段回复正文合成为语音，返回可供前端播放的相对 URL（/data/tts/xxx.mp3）；
    未开启 / 缺 key / 失败一律返回 None，绝不影响文字消息。
    走 MiniMax T2A v2 协议，base_url/api_key/group_id 全 config 可填。
    override：每角色/试听传入的音色覆盖（voice_id/speed/pitch/vol/model），缺省回落 config.tts。"""
    cfg = _tts_cfg()
    if not cfg.get("enabled"):
        return None
    base = (cfg.get("base_url") or "").strip().rstrip("/")
    key = (cfg.get("api_key") or "").strip()
    if not base or not key:
        return None

    # 去掉排版/旁白用的标签残留，但保留 MiniMax 停顿标记 <#x#>（不以 < 后紧跟 # 的不删）
    speak = re.sub(r"<(?!#)[^>]*?>", "", (text or "")).strip()
    # 只读台词：去掉括号旁白后再合成；去完为空说明本条没台词，直接不出声
    if cfg.get("skip_narration"):
        speak = _strip_narration(speak)
    if not speak:
        return None
    if len(speak) > 800:   # 过长截断，避免合成超时/超额
        speak = speak[:800]

    # 角色音色覆盖优先，缺项回落到全局 config.tts
    ov = override or {}
    def _pick(k, d):
        v = ov.get(k)
        return v if v not in (None, "") else cfg.get(k, d)
    voice_id = _pick("voice_id", "female-tianmei")
    model = _pick("model", "speech-01-turbo")
    fmt = str(_pick("format", "mp3")).lower()
    speed = float(_pick("speed", 1.0))
    vol = float(_pick("vol", 1.0))
    pitch = int(_pick("pitch", 0))
    sample_rate = int(_pick("sample_rate", 32000))
    emotion = str(_pick("emotion", "") or "").strip()  # 每条消息的情绪（happy/sad/...），空则不传

    # 缓存键含全部影响音频的参数，避免换音色/语速/情绪后还命中旧文件
    h = hashlib.md5(f"{model}|{voice_id}|{speed}|{vol}|{pitch}|{emotion}|{speak}".encode("utf-8")).hexdigest()
    TTS_DIR.mkdir(parents=True, exist_ok=True)
    out_file = TTS_DIR / f"{h}.{fmt}"
    rel_url = f"/data/tts/{out_file.name}"
    if out_file.exists() and out_file.stat().st_size > 0:
        return rel_url

    url = base + "/t2a_v2"
    gid = (cfg.get("group_id") or "").strip()
    if gid:
        url += f"?GroupId={gid}"
    payload = {
        "model": model,
        "text": speak,
        "stream": False,
        "voice_setting": {
            "voice_id": voice_id,
            "speed": speed,
            "vol": vol,
            "pitch": pitch,
            **({"emotion": emotion} if emotion else {}),
        },
        "audio_setting": {
            "sample_rate": sample_rate,
            "bitrate": 128000,
            "format": fmt,
            "channel": 1,
        },
    }
    try:
        rq = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(rq, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log_print(f"🔇 [TTS] 请求失败: {e}")
        return None

    # MiniMax 标准返回：data.audio = hex 编码音频字节
    data = (result.get("data") or {}) if isinstance(result, dict) else {}
    audio_hex = data.get("audio")
    if not audio_hex:
        log_print(f"🔇 [TTS] 无音频返回: base_resp={result.get('base_resp') if isinstance(result, dict) else result}")
        return None
    try:
        audio_bytes = bytes.fromhex(audio_hex)
    except ValueError:
        try:   # 个别代理返回 base64 而非 hex，兜底再试一次
            audio_bytes = base64.b64decode(audio_hex)
        except Exception:
            log_print("🔇 [TTS] 音频解码失败（既非 hex 也非 base64）")
            return None
    out_file.write_bytes(audio_bytes)
    log_print(f"🔊 [TTS] 合成成功 {voice_id} {len(audio_bytes)}B -> {rel_url}")
    return rel_url


def _attach_tts(msg):
    """给一条 assistant 消息就地挂上 audio 字段；失败静默跳过，文字照常。"""
    try:
        u = synth_tts(msg.get("text", ""))
        if u:
            msg["audio"] = u
    except Exception as e:
        log_print(f"🔇 [TTS] 挂载异常: {e}")
    return msg


def _character_voice(char_name):
    """读某角色 prompt json 里的 voice 覆盖（{voice_id, speed, pitch, ...}）；没有就 {}。"""
    try:
        fp = PROMPTS_DIR / "character" / f"{_safe_name(char_name)}.json"
        if fp.exists():
            d = json.loads(fp.read_text(encoding="utf-8"))
            v = d.get("voice")
            if isinstance(v, dict):
                return v
    except Exception:
        pass
    return {}


def _scene_stamp(session):
    """当前时空坐标，盖在每条 message 上（落库随消息走）。"""
    return {"scene_id": session.current_scene_id,
            "time": session.current_time,
            "place": session.current_place}


def build_scene_block(session):
    """api 模式：拼给 Chat AI 的【当前场景状态 + 信封/转场规约】，注入 tail_anchor。"""
    cur_id = getattr(session, "current_scene_id", GENESIS_SCENE["scene_id"])
    cur_t = getattr(session, "current_time", GENESIS_SCENE["time"])
    cur_p = getattr(session, "current_place", GENESIS_SCENE["place"])
    block = (
        "<current_scene_state>\n"
        f"当前场景ID：{cur_id}\n"
        f"当前剧情时间：{cur_t}\n"
        f"当前所处地点：{cur_p}\n"
        "</current_scene_state>\n"
    )
    return block


SUMMARY_SYSTEM_PROMPT = """你是记忆分析师。对比【已有记忆状态】与【新对话】，只提取新对话里【新增】的、能改变未来互动方式的信息；严格增量，绝不重复已有内容。只输出一个 JSON 对象，不要任何解释或代码围栏。

# 核心要求
- 增量：只记新对话里的新东西，已有记忆已覆盖的绝不重复。
- 面向召回：event.summary 是"高召回的回忆卡片"，不是剧情概括；要让未来用户用一句口语提起这段也能命中。
- 保留原词：人名、原文的称呼/昵称/别称、地点、关键物件/道具、具体动作、情绪态度、关系变化、约定/承诺/交换条件、秘密、暧昧或冲突的钩子——一律保留原文用词，不要改写成抽象同义词。
- 写具体：写清"谁，在什么时间/地点，用什么方式，对谁，做了什么，出现了什么道具，结果如何"。禁止"两人发生冲突""关系升温""气氛暧昧""揭示了一个秘密"这类空话。
- 长度：优先 1 句；信息确实过多再写 2 句，不要拆成空泛铺垫+具体补充。
- 笔触：朴实、白描、有烟火气，避免比喻和文学化修饰。

## summary 反例 → 正例
反例：二人在酒馆发生冲突，关系恶化。
正例：苏晚在黑鹭酒馆当众把欠条拍到顾衡胸口，骂他拿她母亲旧宅做赌注，顾衡想抓她手腕被甩开，赌客起哄，两人彻底撕破脸。
反例：她揭示了一个秘密，对方受到打击。
正例：周柠在浴室门口盯着林雨锁骨上的咬痕逼问昨晚和谁在一起，林雨一边整理湿透的白衬衫一边嘴硬否认，最后答应明晚还去旧码头见她。

# 事件分类
type：相遇=初次接触 / 冲突=对抗误解边界试探情绪顶撞 / 揭示=隐瞒被打破主动坦白无意泄露 / 抉择=站队承诺拒绝回避留白 / 羁绊=关系加深或破裂 / 转变=角色或局势改变 / 收束=问题解决和解 / 日常=共同生活照顾习惯性互动低强度陪伴
weight 与 importance 对应：核心=5(删掉关系理解就断裂) 主线=4(推动当前关系或情绪线) 转折=3(改变关系走向/信任/边界) 点睛=2(有细节不影响主线) 氛围=1(纯氛围)

# 事件时空与因果字段
- scene_id / timeLabel / placeLabel：直接承袭该事件所在【剧本切片】头部标注的 scene 编号与时空标签，原样照抄，不要自己编。
- caused_by：在【过往事件简表】里找引发本次事件的 0~2 个前置事件的整数 ID（如 [11] 或 [11,8]）；无直接因果就写 []。只能填简表里真实出现过的 evt 编号。

# factUpdates（SPO 硬事实增量）
- 只记硬性事实：身份、边界、承诺、偏好、禁忌、物品归属、位置、关系状态等；情绪和剧情不要写成 fact。
- 字段：s=主体 p=谓词 o=值；以 s+p 为键覆盖旧值，只输出新增或变化的条目。
- 关系类谓词用 "对X的看法"（X 为对象人名）。谓词复用已有的，别发明同义词，保持少、硬、稳定。
- isState：若该事实是不可改变的底层设定/生理禁忌/世界观公理/底层称呼（如对芒果过敏、患胃病、称呼对方为宝宝），设 true（容量清理时免疫删除）；寻常偏好/近期小习惯设 false。
- retracted：若剧本中某条旧事实明确失效或被推翻（伤口痊愈、误会解除、约定取消），输出 {"s":"张三","p":"腿伤","retracted":true} 通知删除该条，o 可省略。

# arc（关系弧）
严禁写成散文叙事。格式锁定为：[当前关系终态坐标描述] (进度: X%)。例：处于试探彼此底线、偶尔嘴硬但行动偏袒的暧昧初升期 (进度: 35%)。无变化则空串。

# 输出格式（只输出这一个 JSON）
{"events":[{"scene_id":"scene_12","timeLabel":"Day 2·中午","placeLabel":"食堂二楼","type":"相遇|冲突|揭示|抉择|羁绊|转变|收束|日常","weight":"核心|主线|转折|点睛|氛围","summary":"按上面要求写的高召回回忆卡片","importance":5,"caused_by":[11]}],"factUpdates":[{"s":"主体","p":"谓词","o":"值","isState":false,"retracted":false}],"arc":"[关系终态坐标] (进度: X%)，覆盖式，无变化则空串","session_summary":"当前对话进展的简短滚动摘要，覆盖式，可空"}
没有新东西的字段返回空数组或空字符串；字符串内部避免英文双引号。"""

def run_summary(session, chat_history):
    """增量总结：把一段对话蒸馏成 events/facts/arc/session_summary 落库，并把原文切片入 chunks。"""
    _load_config()
    # 总结可用独立 API（summary_api）；未配 base_url 时回退到聊天用的 api
    sa = config.get("summary_api") or {}
    api_cfg = sa if sa.get("base_url") else config.get("api", {})
    base = api_cfg.get("base_url", "").rstrip("/")
    if not base: return (False, "未配置总结 API")
    url = f"{base}/chat/completions"
    scope = _session_scope(session)
    sid = session.session_id

    # 已有记忆状态
    facts = memory_store.get_facts(scope)
    arc = memory_store.get_summary(f"arc:{scope}") or ""
    sess_sum = memory_store.get_summary(f"session:{sid}") or ""
    state_lines = []
    if arc: state_lines.append(f"关系: {arc}")
    if sess_sum: state_lines.append(f"近况: {sess_sum}")
    for f in facts[:50]:
        state_lines.append(f"事实: {f['subject']} {f['predicate']} {f['object']}")
    state_text = "\n".join(state_lines) or "（暂无）"

    if not any((m.get("text") or "").strip() for m in chat_history):
        return (False, "无新对话")

    # === 前情挂历：双轨召回历史事件（语义远期 Top5 + 时间近期 Top15）合并去重 ===
    tail_text = " ".join((m.get("text") or "") for m in chat_history[-8:]).strip()
    query_vec = embed_query(tail_text) if tail_text else None
    semantic_events = memory_store.recall_events(scope, query_vec=query_vec, k=5) if query_vec else []
    recent_events = memory_store.recall_events(scope, query_vec=None, query_text="", k=15)

    merged_map = {}
    for e in semantic_events:
        if (e.get("score") or 0) >= 0.50:        # 纪委卡死线：低于 0.5 的远期召回不要
            e["_is_distant"] = True
            merged_map[e["id"]] = e
    for e in recent_events:                       # 近期连续性覆盖远期标记
        e["_is_distant"] = False
        merged_map[e["id"]] = e
    sorted_events = sorted(merged_map.values(), key=lambda x: x["id"])

    catalog_lines = []
    for e in sorted_events:
        s_id = e.get("scene_id") or "scene_0"
        t_lbl = e.get("time_label") or "早前"
        p_lbl = e.get("place_label") or "未知地点"
        snippet = (e.get("summary") or "").strip()[:45]
        star = " 🌟[远期伏笔]" if e.get("_is_distant") else ""
        catalog_lines.append(f"[evt-{e['id']}] [{s_id}] ({t_lbl}·{p_lbl}){star} -> {snippet}...")
    catalog_str = "\n".join(catalog_lines) if catalog_lines else "（暂无前置事件记录）"

    # === 待总结剧本：按 scene_id 物理分块渲染 ===
    script_lines = []
    _last_scene = object()
    for m in chat_history:
        t = (m.get("text") or "").strip()
        if not t:
            continue
        s_id = m.get("scene_id") or "scene_0"
        if s_id != _last_scene:
            t_lbl = m.get("time") or "未知时间"
            p_lbl = m.get("place") or "未知地点"
            script_lines.append(f"\n【场景切片：{s_id} ({t_lbl} @ {p_lbl})】")
            _last_scene = s_id
        script_lines.append(f"{m.get('role')}: {t}")
    script_text = "\n".join(script_lines).strip()

    user_content = (f"【已有记忆状态】\n{state_text}\n\n"
                    f"【过往事件简表（用于 caused_by 因果回溯）】\n{catalog_str}\n\n"
                    f"【新对话剧本（已按场景切片）】\n{script_text}")

    payload = {
        "model": api_cfg.get("model", "deepseek-chat"),
        "messages": [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.2,
    }
    try:
        _res = _http_post_json(
            url, payload,
            {"Content-Type": "application/json", "Authorization": f"Bearer {api_cfg.get('api_key')}"},
            timeout=90, tag="总结")
        content = _res["choices"][0]["message"]["content"]
    except Exception as e:
        log_print(f"🧠 [总结失败]: {e}")
        return (False, str(e))

    parsed = _extract_json(content)
    if not parsed:
        log_print("🧠 [总结失败]: 无法解析 JSON")
        return (False, "无法解析 LLM 返回的 JSON")

    def _clean_caused_by(raw):
        """只保留简表里能出现的整数 ID（容忍字符串数字），最多 2 个。"""
        if not isinstance(raw, list):
            return []
        out = []
        for x in raw:
            if isinstance(x, bool):
                continue
            if isinstance(x, (int, float)):
                out.append(int(x))
            elif isinstance(x, str) and x.strip().lstrip("-").isdigit():
                out.append(int(x.strip()))
        return out[:2]

    # events（批量 embed 后落库，带场景时空 + 因果）
    ev_pairs = [(ev, (ev.get("summary") or "").strip())
                for ev in (parsed.get("events") or [])]
    ev_pairs = [(ev, s) for ev, s in ev_pairs if s]
    ev_vecs = embed_texts([s for _, s in ev_pairs]) if ev_pairs else None
    for i, (ev, s) in enumerate(ev_pairs):
        memory_store.upsert_event(
            scope, s, session_id=sid, type=ev.get("type"), weight=ev.get("weight"),
            importance=int(ev.get("importance", 3) or 3),
            caused_by=_clean_caused_by(ev.get("caused_by")),
            scene_id=ev.get("scene_id"), time_label=ev.get("timeLabel"),
            place_label=ev.get("placeLabel"),
            embedding=(ev_vecs[i] if ev_vecs else None))

    # factUpdates（KV 覆盖 / 状态钢印 / 静默抹杀）；兼容旧字段名 facts
    fact_updates = parsed.get("factUpdates")
    if fact_updates is None:
        fact_updates = parsed.get("facts") or []
    for f in fact_updates:
        s = f.get("s") or f.get("subject")
        p = f.get("p") or f.get("predicate")
        o = f.get("o") or f.get("object") or ""
        if not (s and p):
            continue
        if f.get("retracted"):
            memory_store.upsert_fact(scope, str(s), str(p), retracted=True)
        else:
            memory_store.upsert_fact(scope, str(s), str(p), str(o),
                                     is_state=bool(f.get("isState")))
    # 容量卫士：超限时斩首最古老的非核心事实，核心 is_state 免疫
    memory_store.prune_facts(scope)

    # arc / session_summary（覆盖，空则不动）
    if (parsed.get("arc") or "").strip():
        memory_store.upsert_summary(f"arc:{scope}", parsed["arc"].strip())
    if (parsed.get("session_summary") or "").strip():
        memory_store.upsert_summary(f"session:{sid}", parsed["session_summary"].strip())

    # 原文切片入库（细节召回；批量 embed）。保留说话人标签：AI 侧用 RP 角色名，用户侧用用户设定名
    char_label = (session.active_prompts.get("character") or "").strip()
    if char_label in ("", "default"): char_label = "AI"
    user_label = (session.active_prompts.get("user") or "").strip()
    if user_label in ("", "default", "默认"): user_label = "用户"

    chunk_pairs = []  # (speaker, text)
    for m in chat_history:
        t = (m.get("text") or "").strip()
        if len(t) < 8:
            continue
        spk = user_label if m.get("role") == "user" else char_label
        chunk_pairs.append((spk, t))

    ch_vecs = embed_texts([t for _, t in chunk_pairs]) if chunk_pairs else None
    for i, (spk, t) in enumerate(chunk_pairs):
        memory_store.add_chunk(scope, t, session_id=sid, speaker=spk,
                               embedding=(ch_vecs[i] if ch_vecs else None))

    log_print(f"🧠 [记忆更新] scope={scope} +{len(ev_pairs)}事件 / +{len(chunk_pairs)}切片")
    return (True, None)

def _migrate_legacy_memory():
    """一次性幂等迁移：旧 data/sessions/*/memory.json 的 profile → summaries 的 arc 行。"""
    try:
        for d in SESSIONS_DIR.iterdir():
            if not d.is_dir(): continue
            mf = d / "memory.json"
            if not mf.exists(): continue
            try:
                profile = json.loads(mf.read_text(encoding="utf-8")).get("profile", "").strip()
            except Exception:
                continue
            if not profile or profile.startswith("暂时没有"): continue
            s = get_session(d.name)
            scope = _session_scope(s)
            if not memory_store.get_summary(f"arc:{scope}"):
                memory_store.upsert_summary(f"arc:{scope}", profile)
                log_print(f"🧠 [迁移] {d.name} profile → arc:{scope}")
    except Exception as e:
        log_print(f"🧠 [迁移失败]: {e}")

def _summ_batch():
    """每批总结的消息数（可配，默认 16）。"""
    return _memory_cfg().get("summarize_every", 16)

def _summ_meta(sid):
    return memory_store.get_meta(f"summ:{sid}") or {"boundary": 0}

def _set_summ_meta(sid, **kw):
    m = _summ_meta(sid); m.update(kw)
    memory_store.set_meta(f"summ:{sid}", m)

def _needs_summary(session):
    """未总结的消息是否已攒够一批。"""
    b = _summ_meta(session.session_id).get("boundary", 0)
    return (len(session.messages) - b) >= _summ_batch()

def summarize_session(session, full=False):
    """从 boundary 推进总结。full=True 把积压全部补完（分批，带状态）。"""
    sid = session.session_id
    batch = _summ_batch()
    _now = lambda: time.strftime("%Y-%m-%d %H:%M:%S")
    _set_summ_meta(sid, state="running", last_error="")
    try:
        while True:
            with session.lock:
                total = len(session.messages)
                b = _summ_meta(sid).get("boundary", 0)
                if b > total: b = 0   # 消息被清空/缩短 → 重置边界
                window = list(session.messages[b: b + batch])
            # 非 full 需攒够一批；full 时剩余 >=2 条也总结
            if (len(window) < 2) if full else (len(window) < batch):
                break
            ok, err = run_summary(session, window)
            if not ok:
                _set_summ_meta(sid, state="idle", last_status="failed",
                               last_time=_now(), last_error=err or "未知错误")
                return
            b += len(window)
            _set_summ_meta(sid, boundary=b, last_status="success",
                           last_time=_now(), last_error="")
            if not full or b >= total:
                break
            time.sleep(2)   # 缓一拍，避开免费档限速
    finally:
        if _summ_meta(sid).get("state") == "running":
            _set_summ_meta(sid, state="idle")

# ================= 设定助手（原生 tool calling）=================
# 这是一个专用角色：用 OpenAI 原生 tool_calls，帮用户把设定分到「核心(常驻) / 世界书(触发)」、
# 配触发词、整理预设。硬边界：它只能调下面注册的这几个数据层工具，工具实现只碰 data 层，
# 注册表里根本没有「改代码/读写文件」这类工具，所以模型从构造上够不到项目代码。
ASSISTANT_CHAR_KEY = "__assistant__"
ASSISTANT_MAX_ROUNDS = 8

BUILTIN_ASSISTANT_PROMPT = """你是「设定助手」，这个角色扮演陪聊软件的内置配置助理。你的职责是帮用户把一整套设定配好——角色、世界、用户、文风、预设、世界书——让自定义门槛尽量低、陪聊体验尽量好。你不扮演任何角色，用简洁、务实、口语的中文跟用户协作，多给建议、少说废话。

# 软件的设定体系（你必须懂的背景）

注入给聊天 AI 的内容分这么几块，你的工作就是把它们配好：

1. 提示词文件（一类一个具名文件，可复用到多个会话）：
   - character 角色设定(persona)：这个角色是谁——身份、性格、说话方式、动机、与用户的关系。
   - world 世界设定：故事的时代/地点/规则/势力等跨场景稳定的大背景。
   - user 用户设定：用户在故事里是谁、和角色什么关系、怎么称呼。
   - main 主提示词：最顶层的总指令/扮演框架（高级，通常不用动）。
   - style 文风：句子节奏、人称、段落长度、要不要动作神态描写。
   - post 输出规则：硬性约束，比如禁止出戏、不许替用户行动、长度限制。

2. 预设(preset)：把 main + style + post 三个打成一个包。会话绑定预设后，这三项一起生效。
   （注意：main/style/post 不能单独绑给会话，必须通过预设生效；world/user/character 可以直接绑。）

3. 世界书(lore)：角色的设定细节库，分两层，这是注意力优化的关键：
   - 核心层(always_on=true)：角色身份/语气骨架这种「必须永远在场」的，要短，几句话，不需要触发词。
   - 触发层(keyed)：世界观细节、地点、配角、历史背景这种「只在相关场景才需要」的。每条配触发词，聊天里出现触发词、或当前场景地点/时间命中时才注入。

核心切分原则：persona/world/user 放稳定的大框架；零碎的、只在特定场景相关的细节，一律下放成带触发词的世界书条目。别把一大坨细节全塞进 persona——那会稀释聊天 AI 的注意力。

# 你怎么干活
1. 先 list_targets 看有哪些会话可配置，跟用户确认配哪一个，拿到 target_session_id；之后所有绑定/世界书操作都带上它。
2. 动手前先摸现状：list_prompts / get_prompt / list_lore / list_presets，看已有什么，避免覆盖用户辛苦写的东西。
3. 听用户用大白话描述他想要的角色/故事，由你翻译成规范设定：
   - 写 persona / world / user：save_prompt 存文件，再 bind_prompt 绑到目标会话。
   - 配文风：save_prompt 存 style / post，再 save_preset 打成预设，bind_preset 绑给会话。
   - 拆世界书：判断哪些是核心(always_on)、哪些带触发词，用 add_lore 逐条写。
4. 每写一批，简短复述你写了什么、为什么这么分，让用户确认；大改先讲方案再动手。

# 怎么把设定写好（你给用户的专业建议）
- 角色设定：写「具体行为」而非「抽象标签」。不是「她很高冷」，而是「话少，回应常用单字；在意的人面前才会多说，还嘴硬」。给口头禅、习惯动作、底线和软肋、与用户的关系定位。
- 世界设定：只放跨场景不变的规则和背景，别写具体剧情。具体地点/事件下放世界书。
- 用户设定：交代清楚用户的身份、与角色的关系、希望被怎么称呼，这样 AI 不会自说自话。
- 文风：明确人称、句子长短、要不要心理/动作描写、对话与叙述的比例；给一两句范例最有效。
- 输出规则：写成硬约束清单，比如「不替用户说话/行动」「不剧透未发生的事」「单次回复 ≤N 段」。
- 触发词：用聊天里真会出现的专有名词——角色名、地名、物件名，并补上别名/简称，命中率才高。
- 宁可多条短设定，不要一条大杂烩；每条聚焦一个主题，方便单独触发和维护。

# 规矩
- 只能用提供的工具，且只动数据层（提示词文件 / 预设 / 世界书）。你没有、也绝不声称有读写项目代码或任意文件的能力。
- 不要覆盖或删除 default 和系统项；改用户已有内容前先 get 看一眼，别盲写。
- keys 触发词用数组传；常驻条目(always_on)不填 keys。"""


def _assistant_tool_defs():
    """注册给设定助手的工具白名单（OpenAI function schema）。= 硬边界。"""
    target = {"target_session_id": {"type": "string",
              "description": "要配置的会话 id，来自 list_targets。所有设定操作必填。"}}
    return [
        {"type": "function", "function": {
            "name": "list_targets",
            "description": "列出所有可配置的聊天会话（排除设定助手自己）。返回每个会话的 id、角色名。",
            "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {
            "name": "list_lore",
            "description": "列出某会话已有的世界书条目（核心+触发），用于了解现状、避免重复。",
            "parameters": {"type": "object", "properties": dict(target), "required": ["target_session_id"]}}},
        {"type": "function", "function": {
            "name": "add_lore",
            "description": "给某会话新增一条设定。核心设定 always_on=true 不填 keys；细节设定填 keys 触发词。",
            "parameters": {"type": "object", "properties": dict(target, **{
                "title": {"type": "string", "description": "条目标题，如「角色核心」「教学楼三楼」"},
                "content": {"type": "string", "description": "设定正文"},
                "keys": {"type": "array", "items": {"type": "string"},
                         "description": "触发词数组（含别名），如 [\"教学楼\",\"琴房\"]。常驻条目留空。"},
                "always_on": {"type": "boolean", "description": "是否常驻(核心层)。默认 false。"},
                "priority": {"type": "integer", "description": "抢注入预算时的排序权重，默认 0。"}}),
                "required": ["target_session_id", "title", "content"]}}},
        {"type": "function", "function": {
            "name": "update_lore",
            "description": "按 id 部分更新一条设定条目，只改传入字段。",
            "parameters": {"type": "object", "properties": dict(target, **{
                "id": {"type": "integer", "description": "条目 id"},
                "title": {"type": "string"}, "content": {"type": "string"},
                "keys": {"type": "array", "items": {"type": "string"}},
                "always_on": {"type": "boolean"}, "priority": {"type": "integer"}}),
                "required": ["target_session_id", "id"]}}},
        {"type": "function", "function": {
            "name": "delete_lore",
            "description": "按 id 删除一条设定条目。",
            "parameters": {"type": "object", "properties": dict(target, **{
                "id": {"type": "integer", "description": "条目 id"}}),
                "required": ["target_session_id", "id"]}}},
        # ----- 提示词文件（角色/世界/用户/文风/输出规则/主提示词）-----
        {"type": "function", "function": {
            "name": "list_prompts",
            "description": "列出某类提示词文件的名字与显示名。category 取值：character/world/user/main/style/post。",
            "parameters": {"type": "object", "properties": {
                "category": {"type": "string", "enum": PROMPT_CATEGORIES}},
                "required": ["category"]}}},
        {"type": "function", "function": {
            "name": "get_prompt",
            "description": "读取某个提示词文件的正文，改之前先看现状。",
            "parameters": {"type": "object", "properties": {
                "category": {"type": "string", "enum": PROMPT_CATEGORIES},
                "name": {"type": "string", "description": "文件名(英文/拼音标识)，如 xujinwen"}},
                "required": ["category", "name"]}}},
        {"type": "function", "function": {
            "name": "save_prompt",
            "description": "新建或更新一个提示词文件（存在即覆盖）。不能写 default 或系统保留名。写完通常还要 bind 才生效。",
            "parameters": {"type": "object", "properties": {
                "category": {"type": "string", "enum": PROMPT_CATEGORIES},
                "name": {"type": "string", "description": "文件名标识，英文或拼音，唯一"},
                "content": {"type": "string", "description": "提示词正文"},
                "display_name": {"type": "string", "description": "给人看的显示名，如「许今闻」。不填则用 name。"}},
                "required": ["category", "name", "content"]}}},
        {"type": "function", "function": {
            "name": "bind_prompt",
            "description": "把某个 character/world/user 提示词绑定到目标会话使其生效。（main/style/post 不在此绑，要走预设。）",
            "parameters": {"type": "object", "properties": dict(target, **{
                "category": {"type": "string", "enum": ["character", "user"]},
                "name": {"type": "string", "description": "要绑定的提示词文件名"}}),
                "required": ["target_session_id", "category", "name"]}}},
        # ----- 预设（打包 main/style/post）-----
        {"type": "function", "function": {
            "name": "list_presets",
            "description": "列出所有预设及其包含的 main/style/post。",
            "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {
            "name": "get_preset",
            "description": "读取某个预设包含的 main/style/post 引用。",
            "parameters": {"type": "object", "properties": {
                "name": {"type": "string"}}, "required": ["name"]}}},
        {"type": "function", "function": {
            "name": "save_preset",
            "description": "新建或更新一个预设，把 main/style/post 三个提示词文件名打成一个包。这些文件需先用 save_prompt 建好。",
            "parameters": {"type": "object", "properties": {
                "name": {"type": "string", "description": "预设名标识"},
                "main": {"type": "string", "description": "main 提示词文件名，默认 default"},
                "style": {"type": "string", "description": "style 提示词文件名，默认 default"},
                "post": {"type": "string", "description": "post 提示词文件名，默认 default"}},
                "required": ["name"]}}},
        {"type": "function", "function": {
            "name": "bind_preset",
            "description": "把某个预设绑定到目标会话，使其 main/style/post 一起生效。",
            "parameters": {"type": "object", "properties": dict(target, **{
                "name": {"type": "string", "description": "预设名"}}),
                "required": ["target_session_id", "name"]}}},
    ]


def _list_targets():
    """枚举可配置会话（排除助手自身），供 list_targets 工具用。"""
    out = []
    for d in SESSIONS_DIR.iterdir():
        if not d.is_dir():
            continue
        s = get_session(d.name)
        if s.active_prompts.get("character") == ASSISTANT_CHAR_KEY:
            continue
        char = s.active_prompts.get("character", "default")
        name = char
        cfile = PROMPTS_DIR / "character" / f"{_safe_name(char)}.json"
        if cfile.exists():
            try:
                name = json.loads(cfile.read_text(encoding="utf-8")).get("name", char)
            except Exception:
                pass
        out.append({"session_id": d.name, "character": char, "character_name": name})
    return out


PROTECTED_PROMPT_NAMES = {"default", ASSISTANT_CHAR_KEY}


def _read_target_session(args):
    """从工具参数取 target_session_id，做路径安全过滤，返回 (sid, scope) 或抛 ValueError。"""
    sid = "".join(c for c in (args.get("target_session_id") or "") if c.isalnum() or c in "-_")
    if not sid:
        raise ValueError("缺少 target_session_id，请先调用 list_targets 选定要配置的会话")
    return sid, f"sess:{sid}"


def _exec_assistant_tool(name, args):
    """工具派发。返回 JSON 可序列化 dict。= 硬边界（只动数据层提示词/预设/世界书）。"""
    try:
        if name == "list_targets":
            return {"targets": _list_targets()}

        # ---------- 世界书 ----------
        if name == "list_lore":
            _, scope = _read_target_session(args)
            return {"entries": memory_store.list_lore(scope)}
        if name == "add_lore":
            _, scope = _read_target_session(args)
            if not (args.get("title") and args.get("content")):
                return {"error": "title 和 content 必填"}
            rid = memory_store.add_lore(scope, args["title"], args["content"],
                    keys=args.get("keys") or [], priority=args.get("priority", 0),
                    always_on=bool(args.get("always_on")),
                    embedding=_lore_embedding(args["title"], args["content"]))
            return {"ok": True, "id": rid}
        if name == "update_lore":
            _, scope = _read_target_session(args)
            if not args.get("id"):
                return {"error": "id 必填"}
            # 标题或正文有变动才重算 embedding，避免无谓网络请求
            emb = _lore_embedding(args.get("title"), args.get("content")) \
                if (args.get("title") is not None or args.get("content") is not None) else None
            ok = memory_store.update_lore(args["id"], title=args.get("title"),
                    content=args.get("content"), keys=args.get("keys"),
                    priority=args.get("priority"), always_on=args.get("always_on"),
                    embedding=emb)
            return {"ok": ok}
        if name == "delete_lore":
            _read_target_session(args)
            if not args.get("id"):
                return {"error": "id 必填"}
            return {"ok": memory_store.delete_lore(args["id"])}

        # ---------- 提示词文件 ----------
        if name == "list_prompts":
            cat = args.get("category")
            if cat not in PROMPT_CATEGORIES:
                return {"error": f"category 须为 {PROMPT_CATEGORIES} 之一"}
            out = []
            cdir = PROMPTS_DIR / cat
            if cdir.exists():
                for f in sorted(cdir.glob("*.json")):
                    disp = f.stem
                    try:
                        disp = json.loads(f.read_text(encoding="utf-8")).get("name", f.stem)
                    except Exception:
                        pass
                    out.append({"name": f.stem, "display_name": disp})
            return {"prompts": out}
        if name == "get_prompt":
            cat, nm = args.get("category"), _safe_name(args.get("name"))
            if cat not in PROMPT_CATEGORIES or not nm:
                return {"error": "category/name 不合法"}
            fpath = PROMPTS_DIR / cat / f"{nm}.json"
            if not fpath.exists():
                return {"error": "文件不存在"}
            d = json.loads(fpath.read_text(encoding="utf-8"))
            return {"name": nm, "display_name": d.get("name", nm), "content": d.get("content", "")}
        if name == "save_prompt":
            cat, nm = args.get("category"), _safe_name(args.get("name"))
            if cat not in PROMPT_CATEGORIES or not nm:
                return {"error": "category/name 不合法"}
            if nm in PROTECTED_PROMPT_NAMES:
                return {"error": f"{nm} 是保留项，不能覆盖；换个名字"}
            file_data = {"name": args.get("display_name") or nm, "content": args.get("content", "")}
            if cat == "character":
                file_data["avatar"] = args.get("avatar", "")
            (PROMPTS_DIR / cat).mkdir(parents=True, exist_ok=True)
            (PROMPTS_DIR / cat / f"{nm}.json").write_text(
                json.dumps(file_data, ensure_ascii=False, indent=2), encoding="utf-8")
            return {"ok": True, "name": nm}
        if name == "bind_prompt":
            sid, _ = _read_target_session(args)
            cat, nm = args.get("category"), _safe_name(args.get("name"))
            if cat not in ("character", "user") or not nm:
                return {"error": "category 须为 character/user，name 必填"}
            if not (PROMPTS_DIR / cat / f"{nm}.json").exists():
                return {"error": f"{cat}/{nm} 不存在，先 save_prompt"}
            tgt = get_session(sid)
            with tgt.lock:
                tgt.active_prompts[cat] = nm
            tgt.save_active_prompts()
            return {"ok": True}

        # ---------- 预设 ----------
        if name == "list_presets":
            out = []
            if PRESETS_DIR.exists():
                for f in sorted(PRESETS_DIR.glob("*.json")):
                    try:
                        out.append({"name": f.stem, **json.loads(f.read_text(encoding="utf-8"))})
                    except Exception:
                        out.append({"name": f.stem})
            return {"presets": out}
        if name == "get_preset":
            nm = _safe_name(args.get("name"))
            fpath = PRESETS_DIR / f"{nm}.json"
            if not nm or not fpath.exists():
                return {"error": "预设不存在"}
            return {"name": nm, **json.loads(fpath.read_text(encoding="utf-8"))}
        if name == "save_preset":
            nm = _safe_name(args.get("name"))
            if not nm:
                return {"error": "name 必填"}
            if nm in PROTECTED_PROMPT_NAMES:
                return {"error": f"{nm} 是保留项，换个名字"}
            preset = {k: _safe_name(args.get(k)) or "default" for k in PRESET_CATEGORIES}
            PRESETS_DIR.mkdir(parents=True, exist_ok=True)
            (PRESETS_DIR / f"{nm}.json").write_text(
                json.dumps(preset, ensure_ascii=False, indent=2), encoding="utf-8")
            return {"ok": True, "name": nm, "preset": preset}
        if name == "bind_preset":
            sid, _ = _read_target_session(args)
            nm = _safe_name(args.get("name"))
            if not nm or not (PRESETS_DIR / f"{nm}.json").exists():
                return {"error": f"预设 {nm} 不存在，先 save_preset"}
            tgt = get_session(sid)
            with tgt.lock:
                tgt.active_prompts["preset"] = nm
            tgt.save_active_prompts()
            return {"ok": True}

        return {"error": f"未知工具: {name}"}
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}


def call_assistant_api(session_id):
    """设定助手专用：原生 tool_calls 多轮循环。不走 RP 三明治、不触发记忆总结。"""
    session = get_session(session_id)
    _load_config()
    api_cfg = config.get("api", {})

    with session.lock:
        session.is_typing = True
        session.typing_ts = time.time()
        session.pending_event.set()
        recent = list(session.messages[-40:])

    sys_prompt = BUILTIN_ASSISTANT_PROMPT
    targets = _list_targets()
    if targets:
        hint = "；".join(f"{t['session_id']}→{t['character_name']}" for t in targets[:10])
        sys_prompt += f"\n\n[当前可配置的会话] {hint}"

    msgs = [{"role": "system", "content": sys_prompt}]
    for m in recent:
        role = m.get("role")
        if role in ("user", "assistant"):
            msgs.append({"role": role, "content": m.get("text", "")})

    tools = _assistant_tool_defs()
    url = f"{api_cfg.get('base_url', '').rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {api_cfg.get('api_key')}"}
    final_text = ""
    try:
        for rnd in range(ASSISTANT_MAX_ROUNDS):
            payload = {"model": api_cfg.get("model", "deepseek-chat"), "messages": msgs,
                       "tools": tools, "tool_choice": "auto", "temperature": 0.3}
            if rnd > 0:
                time.sleep(0.6)   # 连发之间留个小间隔，别把免费档限速窗口打爆
            res = _http_post_json(url, payload, headers, timeout=90, tag=f"设定助手·{session_id}")
            choice = res["choices"][0]["message"]
            tcs = choice.get("tool_calls") or []
            if not tcs:
                final_text = (choice.get("content") or "").strip()
                break
            # 必须把带 tool_calls 的 assistant 消息回放进上下文，再追加每个 tool 结果
            msgs.append({"role": "assistant", "content": choice.get("content") or "", "tool_calls": tcs})
            for tc in tcs:
                fn = tc.get("function", {}) or {}
                try:
                    a = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    a = {}
                result = _exec_assistant_tool(fn.get("name", ""), a)
                msgs.append({"role": "tool", "tool_call_id": tc.get("id"),
                             "content": json.dumps(result, ensure_ascii=False)})
            log_print(f"🛠️ [设定助手][{session_id}] 第{rnd+1}轮：执行 {len(tcs)} 个工具 "
                      f"({', '.join((t.get('function') or {}).get('name','?') for t in tcs)})")
        else:
            final_text = f"（工具调用到了 {ASSISTANT_MAX_ROUNDS} 轮上限，先停一下。把需求说得更具体我再继续。）"
        if not final_text:
            final_text = "（工具执行完了，但模型没有给出文字回复。）"
    except Exception as e:
        final_text = f"⚠️ 系统提示：{e}"
        log_print(f"[设定助手] 错误: {e}")

    with session.lock:
        session.messages.append({"role": "assistant", "text": final_text,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **_scene_stamp(session)})
        session.is_typing = False
        session.pending_event.clear()
    session.save_messages_async()


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
    char_name = _get_display_name("character", session.active_prompts.get("character"), "AI助手")
    user_name = _get_display_name("user", session.active_prompts.get("user"), "用户")
    header_prompt = _apply_macros(build_header_prompt(session), char_name, user_name)
    memory_str = _apply_macros(build_injected_memory(session, intention or ""), char_name, user_name)
    tail_anchor = _apply_macros(build_tail_anchor(session, memory_str), char_name, user_name)

    with session.lock:
        recent = list(session.messages[-(_memory_cfg().get("recent_rounds", 10) * 2):])
    api_messages = [{"role": "system", "content": header_prompt}]
    for m in recent:
        if m.get("role") in ("user", "assistant"):
            api_messages.append({"role": m["role"], "content": m.get("text", "")})
    directive = (f"（系统提示：现在是你主动联系{user_name}的时机，对方此刻不在对话里。"
                 f"你之前记下的心意/事由：{intention or '想找对方说说话'}。"
                 f"请以{char_name}的身份，主动给{user_name}发一条自然、简短、贴合当前关系与场景的消息；"
                 f"直接说正文，不要解释这是主动消息，不要寒暄式复述。）" + (tail_anchor or ""))
    api_messages.append({"role": "user", "content": directive})

    url = f"{api_cfg.get('base_url', '').rstrip('/')}/chat/completions"
    payload = {"model": api_cfg.get("model", "deepseek-chat"), "messages": api_messages, "temperature": 0.8}
    try:
        res = _http_post_json(
            url, payload,
            {"Content-Type": "application/json", "Authorization": f"Bearer {api_cfg.get('api_key')}"},
            timeout=90, tag="主动消息")
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
    char_name = _get_display_name("character", session.active_prompts.get("character"), "AI助手")
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
    p_msg = {"role": "assistant", "text": text, "proactive": True,
             "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **_scene_stamp(session)}
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
        {"type": "function", "function": {
            "name": "schedule_outreach",
            "description": ("当你（角色）产生了'之后想主动联系用户'的意愿时，用它给自己排一个提醒。"
                            "到点后系统会推送到用户手机。只在确有此意愿时调用，别滥用。"),
            "parameters": {"type": "object", "properties": {
                "kind": {"type": "string", "enum": ["once", "daily", "interval", "idle"],
                         "description": "once=某时刻一次 / daily=每天定点 / interval=每隔一段 / idle=用户久未说话时"},
                "when": {"type": "string", "description": (
                    "触发时机：once 用 'YYYY-MM-DD HH:MM' 或相对量 '+30m'/'+2h'/'+1d'；"
                    "daily 用 'HH:MM'；interval/idle 用分钟数（如 '180' 表示 3 小时）")},
                "mode": {"type": "string", "enum": ["wake", "push"],
                         "description": "wake=到点唤醒你当场组织内容(推荐) / push=直接推固定文案 content"},
                "intention": {"type": "string", "description": "wake 模式：你想说的事由/心情，到点据此生成消息"},
                "content": {"type": "string", "description": "push 模式：到点直接推送的固定文案"}},
                "required": ["kind", "when", "mode"]}}},
        {"type": "function", "function": {
            "name": "list_my_outreach",
            "description": "查看你给本会话排过的主动联系任务。",
            "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {
            "name": "cancel_outreach",
            "description": "按 id 取消一个主动联系任务。",
            "parameters": {"type": "object", "properties": {
                "id": {"type": "integer"}}, "required": ["id"]}}},
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
            job = scheduler.add_job(session_id, kind, when_spec, mode,
                    content=args.get("content", ""), intention=args.get("intention", ""))
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
    session = get_session(session_id)
    _load_config()
    # 设定助手走原生 tool calling 通道，不走下面的 RP 三明治
    if session.active_prompts.get("character") == ASSISTANT_CHAR_KEY:
        return call_assistant_api(session_id)
    api_cfg = config.get("api", {})

    with session.lock:
        session.is_typing = True
        session.typing_ts = time.time()
        session.pending_event.set()
        recent_rounds = _memory_cfg().get("recent_rounds", 10)
        recent_msgs = list(session.messages[-(recent_rounds * 2):])
        current_msg_len = len(session.messages)
        last_user_text = next(
            (m.get("text", "") for m in reversed(session.messages) if m.get("role") == "user"), "")
            
        # === 新增：获取真实的名称 ===
        char_name = _get_display_name("character", session.active_prompts.get("character"), "AI助手")
        user_name = _get_display_name("user", session.active_prompts.get("user"), "用户")

    # 1. 顶部静态人设 (带宏替换)
    header_prompt = _apply_macros(build_header_prompt(session), char_name, user_name)

    # 2. 锁外检索动态记忆 (带宏替换)
    memory_str = _apply_macros(build_injected_memory(session, last_user_text), char_name, user_name)

    # 3. 生成尾部锚定块 (带宏替换)
    tail_anchor = _apply_macros(build_tail_anchor(session, memory_str), char_name, user_name)

    # 4. 极其优雅地拼装三明治上下文
    api_messages = [{"role": "system", "content": header_prompt}]

    for i, m in enumerate(recent_msgs):
        is_last_msg = (i == len(recent_msgs) - 1)
        role = m["role"]
        raw_text = m.get("text", "")

        # === 新增：在对话正文前加上说话人名字 ===
        # prefix = f"{user_name}: " if role == "user" else f"{char_name}: "

        if m.get("image"):
            content_nodes = [
                {"type": "text", "text": (raw_text or "请看这张图片。")},
                {"type": "image_url", "image_url": {"url": m["image"]}}
            ]
            if is_last_msg and tail_anchor:
                content_nodes[0]["text"] += tail_anchor
            api_messages.append({"role": role, "content": content_nodes})
        else:
            content = raw_text
            if is_last_msg and tail_anchor:
                content = f"{content}{tail_anchor}"

            api_messages.append({"role": role, "content": content})

    url = f"{api_cfg.get('base_url','').rstrip('/')}/chat/completions"
    payload = {
        "model": api_cfg.get("model", "deepseek-chat"),
        "messages": api_messages,
        "temperature": 0.7
    }

    session.last_llm_payload = {
        "url": url,
        "model": payload["model"],
        "messages": api_messages,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S")
    }

    sys_preview = " ".join(header_prompt.split())[:40]
    last_u_preview = " ".join(last_user_text.split())[:40]
    mem_lines_count = len(memory_str.splitlines()) if memory_str else 0
    
    log_print(f"↗️ [LLM 请求][{session_id}] ── {payload['model']} ({len(api_messages)}条上下文)")
    log_print(f"   ├─ System: {sys_preview}...")
    if mem_lines_count > 0:
        log_print(f"   ├─ 记忆块: 实时注入了 {mem_lines_count} 行长时上下文")
    log_print(f"   └─ User  : {last_u_preview}...")

    # agentic：开启后给角色挂上「自助排程」工具（tool_choice=auto，平时不调用、零影响）
    if _outreach_enabled():
        payload["tools"] = _outreach_tool_defs()
        payload["tool_choice"] = "auto"

    def _post(pl):
        return _http_post_json(
            url, pl,
            {"Content-Type": "application/json", "Authorization": f"Bearer {api_cfg.get('api_key')}"},
            timeout=90, tag=f"聊天·{session_id}")

    try:
        raw_reply = ""
        for _round in range(5):
            res_data = _post(payload)
            choice = res_data['choices'][0]['message']
            tcs = choice.get('tool_calls') or []
            if not tcs:
                raw_reply = (choice.get('content') or '').strip()
                break
            # 角色调了排程工具：执行，并把 assistant(tool_calls)+tool 结果回放进上下文再续一轮
            api_messages.append({"role": "assistant", "content": choice.get('content') or "", "tool_calls": tcs})
            for tc in tcs:
                fn = tc.get('function', {}) or {}
                try:
                    a = json.loads(fn.get('arguments') or '{}')
                except Exception:
                    a = {}
                r = _exec_outreach_tool(fn.get('name', ''), a, session_id)
                api_messages.append({"role": "tool", "tool_call_id": tc.get('id'),
                                     "content": json.dumps(r, ensure_ascii=False)})
            log_print(f"🛠️ [角色排程][{session_id}] 执行 {len(tcs)} 个工具")

        # 解析信封：推进场景闩锁，只把干净 <content> 落库/发前端
        reply_text, _meta = ingest_reply(session, raw_reply)

        a_msg = {
            "role": "assistant",
            "text": reply_text,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            **_scene_stamp(session),
        }
        if _meta.get("emotion"):    a_msg["emotion"] = _meta["emotion"]
        if _meta.get("voice_text"): a_msg["voice_text"] = _meta["voice_text"]
        with session.lock:
            session.messages.append(a_msg)
            session.is_typing = False
            session.pending_event.clear()
        session.save_messages_async()
        log_print(f"\n{'='*15} 🤖 [AI 回复 -> Session: {session_id}] {'='*15}")
        log_print(raw_reply)
        log_print(f"{'='*55}\n")

        if _needs_summary(session):
            log_print(f"🧠 [记忆触发] 增量总结 -> Session: {session_id}")
            threading.Thread(target=summarize_session, args=(session,)).start()

    except Exception as e:
        log_print(f"[API] 发生错误: {e}")
        with session.lock:
            session.messages.append({
                "role": "assistant",
                "text": f"⚠️ 系统提示：{str(e)}",
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })
            session.is_typing = False
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
        "/api/prompts/save", "/api/prompts/delete",
        "/api/sessions/create", "/api/sessions/delete",
        "/api/sessions/rename", "/api/sessions/clone", "/api/sessions/pin",
        "/api/presets/save", "/api/presets/delete",
        "/api/toggle_mode",
        "/api/config/save", "/api/test_models", # ⭐️ 新增：保存配置与拉取模型的路由
        "/api/notify/test",
        "/api/tts/option",  # 语音小开关（skip_narration 等），无会话副作用
    }
    NO_SESSION_GET_PATHS = {
        "/api/sessions/list", "/api/presets/list", "/api/presets/get",
        "/api/config", # ⭐️ 新增：读取全局配置的路由
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
        if path.startswith("/api/") and path not in self.AUTH_PUBLIC_PATHS and not self._check_auth():
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
        session = None if path in self.NO_SESSION_POST_PATHS else get_session(session_id)

        if path == "/api/submit":
            length = int(self.headers.get("Content-Length", 0))
            body = _safe_decode(self.rfile.read(length))
            try: data = json.loads(body)
            except: data = {"text": body}

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
                except Exception as e: log_print(f"[警告] 图片保存失败: {e}")

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
            msg_to_save = {"role": "user", "text": text, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                           **_scene_stamp(session)}
            if image_data: msg_to_save["image"] = image_data

            with session.lock:
                session.messages.append(msg_to_save)
                # 👇 修复：在这个绝对同步的环节立刻挂上锁，防止前端由于时间差读到 False
                session.is_typing = True
                session.typing_ts = time.time()
                # pending 是前端等待锁的唯一判据：从用户发送这一刻起就 pending，
                # 不管 api/claude_mode，直到真正有回复（或中断/清空）才解除，不依赖 is_typing 的 120s 超时。
                session.pending_event.set()
            session.save_active_prompts() # (注意原有代码这里可能是 save_messages_async，保持你的原有调用即可)
            session.save_messages_async()
            clean_input = " ".join(text.split())
            log_print(f"📥 [用户输入][{session_id}]: {clean_input[:35]}...")

            if config.get("mode") == "api":
                threading.Thread(target=call_llm_api, args=(session_id,)).start()
            else:
                with session.lock: session.pending_text = text
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
            session.save_messages_async()
            self._json({"ok": True})

        elif path == "/api/reply":
            length = int(self.headers.get("Content-Length", 0))
            body = _safe_decode(self.rfile.read(length))
            _rbody = json.loads(body) if "{" in body else {"text": body}
            reply_txt = (_rbody.get("text", "") or "").strip()
            is_proactive = bool(_rbody.get("proactive"))
            # claude_mode 回复同样走信封解析：推进场景闩锁，只落干净正文
            reply_txt, _meta = ingest_reply(session, reply_txt)
            msg = {"role": "assistant", "text": reply_txt,
                   "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **_scene_stamp(session)}
            if is_proactive:
                msg["proactive"] = True
            if _meta.get("emotion"):    msg["emotion"] = _meta["emotion"]
            if _meta.get("voice_text"): msg["voice_text"] = _meta["voice_text"]
            with session.lock:
                session.messages.append(msg)
                # 👇 修复：外部写入消息后，必须解除打字状态并清空等待事件
                session.is_typing = False
                session.pending_event.clear()
            session.save_messages_async()
            # 主动消息（调度唤醒角色组织的）→ 推送到手机
            if is_proactive:
                char_name = _get_display_name("character", session.active_prompts.get("character"), "AI助手")
                ok, detail = _push_notify(char_name, reply_txt)
                log_print(f"🔔 [主动·claude_mode→{session.session_id}] 推送 ok={ok} ({detail})")
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
                override = dict(voice_override)              # 试听：整体覆盖
            else:
                char_name = session.active_prompts.get("character") if session else None
                override = dict(_character_voice(char_name)) if char_name else {}
                em = data.get("emotion")                     # 该条消息的情绪叠加到角色音色上
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
            with session.lock: session.pending_event.clear()
            self._json({"ok": True})
            
        elif path == "/api/interrupt":
            with session.lock:
                session.is_typing = False
                session.pending_event.clear()
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
                if idx is not None and 0 <= idx < len(session.messages) and session.messages[idx]["role"] == "assistant":
                    session.messages.pop(idx)
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
                if cat == "character":
                    file_data["avatar"] = data.get("avatar", "")
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
                fpath.write_text(json.dumps(file_data, ensure_ascii=False, indent=2), encoding="utf-8")
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
                if fpath.exists(): fpath.unlink()
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
                    if k in SESSION_BINDING_KEYS: session.active_prompts[k] = _safe_name(v) or "default"
            session.save_active_prompts()
            self._json({"ok": True})

        # ================= 新增：会话管理 =================
        elif path == "/api/sessions/create":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(_safe_decode(self.rfile.read(length)))
            character = _safe_name(data.get("character")) or "default"
            char_label = character
            cfile = PROMPTS_DIR / "character" / f"{character}.json"
            if cfile.exists():
                try: char_label = json.loads(cfile.read_text(encoding="utf-8")).get("name", character)
                except: pass
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
                memory_store.delete_scope(f"sess:{del_id}", del_id)  # 连带清掉该会话记忆
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
                    memory_store.migrate_scope(f"sess:{old_id}", f"sess:{new_id}", old_id, new_id)
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
                memory_store.fork_scope(f"sess:{src_id}", f"sess:{new_id}", src_id, new_id)
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
                    try: meta = json.loads(meta_file.read_text(encoding="utf-8"))
                    except: pass
                meta["pinned"] = not meta.get("pinned", False)
                meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
                self._json({"ok": True, "pinned": meta["pinned"]})

        # ================= 新增：预设管理 (main+style+post 打包) =================
        elif path == "/api/presets/save":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(_safe_decode(self.rfile.read(length)))
            name = _safe_name(data.get("name"))
            if not name:
                self._json({"ok": False, "error": "Invalid params"})
            else:
                preset = {k: _safe_name(data.get(k)) or "default" for k in PRESET_CATEGORIES}
                (PRESETS_DIR / f"{name}.json").write_text(json.dumps(preset, ensure_ascii=False, indent=2), encoding="utf-8")
                self._json({"ok": True})

        elif path == "/api/presets/delete":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(_safe_decode(self.rfile.read(length)))
            name = _safe_name(data.get("name"))
            if name == "default":
                self._json({"ok": False, "error": "默认预设不可删除"})
            elif name:
                fpath = PRESETS_DIR / f"{name}.json"
                if fpath.exists(): fpath.unlink()
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
                config.update(new_cfg) # 深度更新内存中的 config
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
            target_url = f"{base_url}/models" if not base_url.endswith("/models") else base_url
            try:
                req = urllib.request.Request(target_url, headers={"Authorization": f"Bearer {api_key}"})
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
            ok, detail = _push_notify("Chat Bridge 测试推送",
                "如果你在手机上看到这条，推送通道就通了 ✅")
            icon_url = ((_resolved_notify_cfg().get("bark") or {}).get("icon") or "(无)")
            log_print(f"🔔 [推送测试] ok={ok} detail={detail}")
            log_print(f"🔔 [推送测试] 图标URL={icon_url} —— 用手机浏览器打开它，能看到图才说明手机够得到")
            self._json({"ok": ok, "detail": detail, "icon": icon_url})

        elif path == "/api/memory/summarize":
            threading.Thread(target=summarize_session, args=(session,), kwargs={"full": True}).start()
            self._json({"ok": True})

        elif path == "/api/memory/edit":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(_safe_decode(self.rfile.read(length)))
            table, rid = data.get("table"), data.get("id")
            ok = False
            if table == "events":
                emb = embed_query(data["summary"]) if data.get("summary") else None
                ok = memory_store.update_event(rid, summary=data.get("summary"),
                        type=data.get("type"), weight=data.get("weight"),
                        importance=data.get("importance"), embedding=emb)
            elif table == "facts":
                ok = memory_store.update_fact(rid, subject=data.get("subject"),
                        predicate=data.get("predicate"), obj=data.get("object"),
                        is_state=data.get("is_state"))
            elif table == "summaries" and data.get("key"):
                memory_store.upsert_summary(data["key"], data.get("text", "")); ok = True
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
                    memory_store.worldbook_scope(book_id), title, content,
                    keys=data.get("keys") or [],
                    priority=data.get("priority", 0),
                    always_on=bool(data.get("always_on")),
                    embedding=_lore_embedding(title, content))
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
                    bid, name=data.get("name"),
                    bind_type=data.get("bind_type"),
                    bind_target=data.get("bind_target"))
                self._json({"ok": ok})

        elif path == "/api/worldbooks/delete":
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(_safe_decode(self.rfile.read(length)))
            bid = data.get("id")
            self._json({"ok": memory_store.delete_worldbook(bid) if bid is not None else False})

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
                emb = _lore_embedding(data.get("title"), data.get("content")) \
                    if (data.get("title") is not None or data.get("content") is not None) else None
                ok = memory_store.update_lore(
                    rid, title=data.get("title"), content=data.get("content"),
                    keys=data.get("keys"), priority=data.get("priority"),
                    always_on=data.get("always_on"), embedding=emb)
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
            scope = memory_store.worldbook_scope(book_id) if book_id is not None \
                else _session_scope(session)
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
                job = scheduler.add_job(session.session_id, kind, when_spec, mode,
                        content=data.get("content", ""), intention=data.get("intention", ""))
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
            self._json({"ok": scheduler.set_enabled(rid, bool(data.get("enabled"))) if rid else False})

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
            with session.lock: self._json(session.messages)
            return
        
        if path == "/api/config":
            with config_lock:
                self._json(config)
            return

        if path == "/api/sessions/list":
            s_list = []
            char_cache = {}
            for d in SESSIONS_DIR.iterdir():
                if not d.is_dir(): continue
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
                        "avatar": cdata.get("avatar", "")
                    }
                meta = char_cache[char_name]

                preview = ""
                if s.messages:
                    last = s.messages[-1]
                    preview = "[图片]" if last.get("image") else last.get("text", "")[:30]

                updated_at = s.messages_file.stat().st_mtime if s.messages_file.exists() else 0

                pinned = False
                meta_file = d / "meta.json"
                if meta_file.exists():
                    try: pinned = json.loads(meta_file.read_text(encoding="utf-8")).get("pinned", False)
                    except: pass

                s_list.append({
                    "id": d.name,
                    "character": char_name,
                    "character_name": meta["name"],
                    "avatar": meta["avatar"],
                    "preview": preview,
                    "updated_at": updated_at,
                    "pinned": pinned
                })
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
                self._json({"ok": True, "data": json.loads(fpath.read_text(encoding="utf-8"))})
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
                    char_list.append({
                        "key": f.stem,
                        "name": cdata.get("name", f.stem),
                        "avatar": cdata.get("avatar", "")
                    })
                except:
                    char_list.append({"key": f.stem, "name": f.stem, "avatar": ""})

            # 用户分身（“我”页主名片 + 平行分身）：解析 user 类别下的完整资料
            user_list = []
            for f in sorted((PROMPTS_DIR / "user").glob("*.json")):
                try:
                    udata = json.loads(f.read_text(encoding="utf-8"))
                    user_list.append({
                        "key": f.stem,
                        "name": udata.get("name", f.stem),
                        "avatar": udata.get("avatar", ""),
                        "content": udata.get("content", ""),
                    })
                except:
                    user_list.append({"key": f.stem, "name": f.stem, "avatar": "", "content": ""})
            # default 排在最前，作为主身份
            user_list.sort(key=lambda u: (u["key"] != "default", u["key"]))

            self._json({
                "tree": tree,
                "active": session.active_prompts,
                "characters": char_list,
                "users": user_list
            })
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
                        text = (f"（系统·主动联系时机：现在请你以角色身份主动给用户发一条消息，"
                                f"对方此刻不在对话里。事由/心情：{pro}。直接说正文，自然简短，"
                                f"贴合当前关系与场景，不要解释这是主动消息。回复请照常用 <msg> 信封，"
                                f"并在 /api/reply 时附带 proactive:true。）")
                        self._json({"pending": True, "text": text, "proactive": True,
                                    "session_id": found.session_id,
                                    "scene": {"scene_id": found.current_scene_id,
                                              "time": found.current_time,
                                              "place": found.current_place}})
                    else:
                        self._json({"pending": True, "text": found.pending_text,
                                    "session_id": found.session_id,
                                    "scene": {"scene_id": found.current_scene_id,
                                              "time": found.current_time,
                                              "place": found.current_place}})
            else:
                self._json({"pending": False})
            return

        # ====== 这是补回来的打字状态专用接口 ======
        if path == "/api/typing_status":
            with session.lock:
                if session.is_typing and (time.time() - session.typing_ts > 120): # 改为 120
                    session.is_typing = False
                # pending 不受 120s 超时影响：只有真正回复/中断/清空才会解除，是前端解锁等待态的唯一依据
                self._json({"typing": session.is_typing, "pending": session.pending_event.is_set()})
            return

        if path == "/api/status":
            last_user_ts = last_any_ts = None
            with session.lock:
                for m in reversed(session.messages):
                    if last_any_ts is None: last_any_ts = m.get("ts")
                    if m["role"] == "user" and last_user_ts is None: last_user_ts = m.get("ts")
                    if last_user_ts and last_any_ts: break

                if session.is_typing and (time.time() - session.typing_ts > 120): # 改为 120
                    session.is_typing = False

                self._json({
                    "message_count": len(session.messages),
                    "last_user_ts": last_user_ts,
                    "last_any_ts": last_any_ts,
                    "pending": session.pending_event.is_set(),
                    "typing": session.is_typing,
                    "mode": config.get("mode", "api")
                })
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
                self._json({"lore": memory_store.list_lore(
                    memory_store.worldbook_scope(book_id))})
            return

        if path == "/api/worldbooks/list":
            # 全局：列出所有世界书（含条目数）
            self._json({"worldbooks": memory_store.list_worldbooks()})
            return

        if path == "/api/worldbooks/session":
            # 本会话视角：自动并入（角色/用户绑定）的 + 手动挂载的 + 全部可选书
            char = (session.active_prompts.get("character") or "default")
            user = (session.active_prompts.get("user") or "default")
            manual = set(getattr(session, "active_worldbooks", []) or [])
            books = memory_store.list_worldbooks()
            auto, others = [], []
            for wb in books:
                bt, tgt = wb.get("bind_type"), wb.get("bind_target")
                if bt == "character" and tgt == char:
                    wb = {**wb, "auto_reason": "character"}; auto.append(wb)
                elif bt == "user" and tgt == user:
                    wb = {**wb, "auto_reason": "user"}; auto.append(wb)
                else:
                    others.append(wb)
            self._json({
                "character": char, "user": user,
                "auto": auto, "others": others,
                "manual_ids": sorted(manual),
            })
            return

        if path == "/api/outreach":
            # 列出当前会话的主动联系任务
            self._json({"jobs": scheduler.list_jobs(session.session_id)})
            return

        if path == "/api/memory/search":
            q = query.get("q", [""])[0]
            try: k = int(query.get("k", ["5"])[0])
            except: k = 5
            scope = _session_scope(session)
            qv = embed_query(q) if q else None
            self._json({
                "events": memory_store.recall_events(scope, query_vec=qv, query_text=q, k=k),
                "chunks": memory_store.recall_chunks(scope, query_vec=qv, query_text=q, k=k),
            })
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
            self._json({
                "scope": scope,
                "counts": {"events": len(events), "facts": len(facts), "chunks": len(chunks)},
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
            })
            return

        super().do_GET()

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        
        # 👇 新增下面这 3 行，强制浏览器不缓存 API 结果
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        # 👆 ------------------------------------------
        
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args): pass

if __name__ == "__main__":
    _init_dirs()
    _load_config()
    memory_store.init_db(str(MEMORY_DB))
    scheduler.init_db(str(JOBS_DB))
    _migrate_legacy_memory()
    # 主动联系调度线程：每 60s 扫一次到点任务，触发 _fire_outreach
    threading.Thread(target=scheduler.run_loop,
                     kwargs={"on_fire": _fire_outreach, "get_last_user_ts": _get_last_user_ts, "interval": 60},
                     daemon=True).start()
    log_print("⏰ [调度] 主动联系调度线程已启动（60s 轮询）")
    local_ip = _detect_lan_ip()
    LAN_BASE = f"http://{local_ip}:{PORT}"  # 推送图标相对路径据此补成绝对 URL

    print(f"\n" + "="*50)
    print(f" 🚀 Chat Server (Multi-Session & Modular Prompt)")
    print(f" Current Mode -> {config.get('mode').upper()}")
    print(f" Web UI URL   -> http://{local_ip}:{PORT}")
    print(f" Sessions Dir -> {SESSIONS_DIR}")
    print(f" Prompts Dir  -> {PROMPTS_DIR}")
    print("="*50 + "\n")

    for d in SESSIONS_DIR.iterdir():
        if d.is_dir(): get_session(d.name)
    get_session("default")
    log_print(f"📁 [本地缓存] 已静默预载 {len(sessions_map)} 个角色 Session")

    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log_print("正在关闭服务器...")
    finally:
        server.shutdown()
