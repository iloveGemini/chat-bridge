"""
Chat Bridge Server (Dual Engine + Long-Term Auto Memory + Vision + Multi-Session + Modular Prompts)
Usage: python server.py [port]
"""
import http.server
import json
import sys
import time
import shutil
import threading
import urllib.request
import urllib.error
import base64
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import memory_store

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

# "预设" 只打包这三个分类
PRESET_CATEGORIES = ["main", "style", "post"]

MAX_MESSAGES = 100
KEEP_RECENT = 50

# 提示词模块大类
PROMPT_CATEGORIES = ["main", "world", "character", "user", "style", "post"]

# 会话实际绑定的 4 个选择（只存名字）。preset 在生成时展开成 main/style/post。
SESSION_BINDING_KEYS = ["preset", "world", "user", "character"]

# 全局配置和锁
config = {}
config_lock = threading.Lock()
sessions_map = {} # 内存中驻留的会话对象 { session_id: SessionObject }

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
        "world":     {"name": "默认", "content": ""},
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
        config.setdefault("memory", {"recent_rounds": 10, "summarize_every": 16, "recall_n": 30, "top_k": 5})

def _save_config():
    with config_lock:
        CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

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

    w = _read_prompt_content("world", active.get("world", "default"))
    if w:
        parts.append(f"<world_setting>\n{w}\n</world_setting>")

    c = _read_prompt_content("character", active.get("character", "default"))
    if c:
        parts.append(f"<persona>\n{c}\n</persona>")

    u = _read_prompt_content("user", active.get("user", "default"))
    if u:
        parts.append(f"<target_user>\n{u}\n</target_user>")

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

        self.lock = threading.Lock()
        self.messages = []
        self.pending_event = threading.Event()
        self.pending_text = ""
        self.is_typing = False
        self.typing_ts = 0
        # 会话绑定：只存 4 个名字。preset 默认 default，world/user/character 默认 default。
        self.active_prompts = {k: "default" for k in SESSION_BINDING_KEYS}
        self.last_llm_payload = None

        self.load_messages()
        self.load_active_prompts()
        log_print(f"[会话加载] Session就绪: {session_id}")

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

    def save_messages_async(self):
        with self.lock:
            self._ensure_dir()
            if len(self.messages) > MAX_MESSAGES:
                overflow = self.messages[:-KEEP_RECENT]
                self.messages = self.messages[-KEEP_RECENT:]
                ts = time.strftime("%Y-%m-%d_%H-%M-%S")
                archive_file = ARCHIVE_DIR / f"{self.session_id}_{ts}.json"
                try:
                    archive_file.write_text(json.dumps(overflow, ensure_ascii=False, indent=2), encoding="utf-8")
                except: pass

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
    """记忆作用域：按角色隔离，同角色跨会话共享。"""
    return f"char:{session.active_prompts.get('character', 'default')}"

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

def build_injected_memory(session, query_text):
    """组装注入 system prompt 的记忆块（facts+arc+近况+召回事件/切片），两模式共用。"""
    scope = _session_scope(session)
    qv = embed_query(query_text) if query_text else None
    mcfg = _memory_cfg()
    return memory_store.build_memory_context(
        scope, session.session_id,
        query_vec=qv, query_text=query_text or "",
        top_k=mcfg.get("top_k", 5), recall_n=mcfg.get("recall_n", 30))

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

# facts（SPO 硬事实）
- 只记硬性事实：身份、边界、承诺、偏好、禁忌、物品归属、位置、关系状态等；情绪和剧情不要写成 fact。
- KV 覆盖：以 subject+predicate 为键，新值覆盖旧值；只输出新增或变化的条目。
- 关系类谓词用 "对X的看法"（X 为对象人名）。
- 谓词复用已有的，别发明同义词，保持少、硬、稳定。

# 输出格式（只输出这一个 JSON）
{"events":[{"type":"相遇|冲突|揭示|抉择|羁绊|转变|收束|日常","weight":"核心|主线|转折|点睛|氛围","summary":"按上面要求写的高召回回忆卡片","importance":5}],"facts":[{"subject":"主体","predicate":"谓词","object":"值"}],"arc":"一句话概括当前角色关系走向，覆盖式，无变化则空串","session_summary":"当前对话进展的简短滚动摘要，覆盖式，可空"}
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

    history_text = "\n".join(
        f"{m.get('role')}: {m.get('text', '')}" for m in chat_history if m.get("text"))
    if not history_text.strip(): return (False, "无新对话")

    payload = {
        "model": api_cfg.get("model", "deepseek-chat"),
        "messages": [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": f"【已有记忆状态】\n{state_text}\n\n【新对话】\n{history_text}"},
        ],
        "temperature": 0.2,
    }
    try:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_cfg.get('api_key')}"})
        with urllib.request.urlopen(req, timeout=90) as resp:
            content = json.loads(resp.read().decode("utf-8"))["choices"][0]["message"]["content"]
    except Exception as e:
        log_print(f"🧠 [总结失败]: {e}")
        return (False, str(e))

    parsed = _extract_json(content)
    if not parsed:
        log_print("🧠 [总结失败]: 无法解析 JSON")
        return (False, "无法解析 LLM 返回的 JSON")

    # events（批量 embed 后落库）
    ev_pairs = [(ev, (ev.get("summary") or "").strip())
                for ev in (parsed.get("events") or [])]
    ev_pairs = [(ev, s) for ev, s in ev_pairs if s]
    ev_vecs = embed_texts([s for _, s in ev_pairs]) if ev_pairs else None
    for i, (ev, s) in enumerate(ev_pairs):
        memory_store.upsert_event(
            scope, s, session_id=sid, type=ev.get("type"), weight=ev.get("weight"),
            importance=int(ev.get("importance", 3) or 3),
            embedding=(ev_vecs[i] if ev_vecs else None))
    # facts（KV 覆盖）
    for f in (parsed.get("facts") or []):
        s, p, o = f.get("subject"), f.get("predicate"), f.get("object")
        if s and p and o:
            memory_store.upsert_fact(scope, str(s), str(p), str(o))
    # arc / session_summary（覆盖，空则不动）
    if (parsed.get("arc") or "").strip():
        memory_store.upsert_summary(f"arc:{scope}", parsed["arc"].strip())
    if (parsed.get("session_summary") or "").strip():
        memory_store.upsert_summary(f"session:{sid}", parsed["session_summary"].strip())

    # 原文切片入库（细节召回；批量 embed）
    chunk_texts = [(m.get("text") or "").strip() for m in chat_history]
    chunk_texts = [t for t in chunk_texts if len(t) >= 8]
    ch_vecs = embed_texts(chunk_texts) if chunk_texts else None
    for i, t in enumerate(chunk_texts):
        memory_store.add_chunk(scope, t, session_id=sid,
                               embedding=(ch_vecs[i] if ch_vecs else None))

    log_print(f"🧠 [记忆更新] scope={scope} +{len(ev_pairs)}事件 / +{len(chunk_texts)}切片")
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

def call_llm_api(session_id):
    session = get_session(session_id)
    _load_config()
    api_cfg = config.get("api", {})

    with session.lock:
        session.is_typing = True
        session.typing_ts = time.time()
        recent_rounds = _memory_cfg().get("recent_rounds", 10)
        recent_msgs = list(session.messages[-(recent_rounds * 2):])
        current_msg_len = len(session.messages)
        last_user_text = next(
            (m.get("text", "") for m in reversed(session.messages) if m.get("role") == "user"), "")

    # 1. 顶部静态人设 (退出 with 锁缩进，回到 4 空格)
    header_prompt = build_header_prompt(session)

    # 2. 锁外检索动态记忆
    memory_str = build_injected_memory(session, last_user_text)

    # 3. 生成尾部锚定块
    tail_anchor = build_tail_anchor(session, memory_str)

    # 4. 极其优雅地拼装三明治上下文
    api_messages = [{"role": "system", "content": header_prompt}]

    for i, m in enumerate(recent_msgs):
        is_last_msg = (i == len(recent_msgs) - 1)
        role = m["role"]
        raw_text = m.get("text", "")

        if m.get("image"):
            content_nodes = [
                {"type": "text", "text": raw_text or "请看这张图片。"},
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

    log_print(f"═══ LLM Request [{session_id}] ═══")
    for m in api_messages:
        role = m["role"]
        content = m["content"] if isinstance(m["content"], str) else "[multimodal]"
        preview = content[:200] + ("..." if len(content) > 200 else "")
        log_print(f"  [{role}] {preview}")
    log_print(f"═══ End ({len(api_messages)} messages, model={payload['model']}) ═══")

    try:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode('utf-8'),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_cfg.get('api_key')}"}
        )
        with urllib.request.urlopen(req, timeout=90) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            reply_text = res_data['choices'][0]['message']['content'].strip()

            with session.lock:
                session.messages.append({
                    "role": "assistant",
                    "text": reply_text,
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                })
                session.is_typing = False
            session.save_messages_async()
            log_print(f"🤖 [API] 模型回复完成 -> Session: {session_id}")

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
        session.save_messages_async()

# ================= HTTP 服务 =================
class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    # 这些路径不依赖/不应该触发 get_session() 的自动新建副作用
    NO_SESSION_POST_PATHS = {
        "/api/prompts/save", "/api/prompts/delete",
        "/api/sessions/create", "/api/sessions/delete",
        "/api/sessions/rename", "/api/sessions/clone", "/api/sessions/pin",
        "/api/presets/save", "/api/presets/delete",
        "/api/toggle_mode",
        "/api/config/save", "/api/test_models", # ⭐️ 新增：保存配置与拉取模型的路由
    }
    NO_SESSION_GET_PATHS = {
        "/api/sessions/list", "/api/presets/list", "/api/presets/get",
        "/api/config", # ⭐️ 新增：读取全局配置的路由
    }

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        session_id = query.get("session_id", ["default"])[0]
        session = None if path in self.NO_SESSION_POST_PATHS else get_session(session_id)

        _load_config()

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

            msg_to_save = {"role": "user", "text": text, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
            if image_data: msg_to_save["image"] = image_data

            with session.lock:
                session.messages.append(msg_to_save)
                # 👇 修复：在这个绝对同步的环节立刻挂上锁，防止前端由于时间差读到 False
                session.is_typing = True
                session.typing_ts = time.time()
            session.save_active_prompts() # (注意原有代码这里可能是 save_messages_async，保持你的原有调用即可)
            session.save_messages_async()
            log_print(f"📥 [新消息][{session_id}]: {text[:20]}...")

            if config.get("mode") == "api":
                threading.Thread(target=call_llm_api, args=(session_id,)).start()
            else:
                with session.lock: session.pending_text = text
                session.pending_event.set()

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
            reply_txt = json.loads(body).get("text", "").strip() if "{" in body else body
            with session.lock:
                session.messages.append({"role": "assistant", "text": reply_txt, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})
                # 👇 修复：外部写入消息后，必须解除打字状态并清空等待事件
                session.is_typing = False
                session.pending_event.clear()
            session.save_messages_async()
            # claude_mode 的回复不走 call_llm_api，总结触发器需在此补上
            if _needs_summary(session):
                threading.Thread(target=summarize_session, args=(session,)).start()
            self._json({"ok": True})

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
                if user_text: session.pending_event.set()
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
                if cat == "character":
                    file_data["avatar"] = data.get("avatar", "")
                fpath = PROMPTS_DIR / cat / f"{name}.json"
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
                        predicate=data.get("predicate"), obj=data.get("object"))
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

        else:
            self._json({"ok": False, "error": "not found"}, 404)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
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
                    
            self._json({
                "tree": tree,
                "active": session.active_prompts,
                "characters": char_list
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
            # 🔥 真正阻塞等待，零消耗挂机（不再忙轮询）
            if session.pending_event.wait(timeout=86400):
                with session.lock:
                    self._json({"pending": True, "text": session.pending_text, "session_id": session.session_id})
            else:
                self._json({"pending": False})
            return

        # ====== 这是补回来的打字状态专用接口 ======
        if path == "/api/typing_status":
            with session.lock:
                if session.is_typing and (time.time() - session.typing_ts > 120): # 改为 120
                    session.is_typing = False
                self._json({"typing": session.is_typing})
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
    _migrate_legacy_memory()
    import socket
    local_ip = socket.gethostbyname(socket.gethostname())

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

    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log_print("正在关闭服务器...")
    finally:
        server.shutdown()
