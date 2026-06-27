# -*- coding: utf-8 -*-
"""会话领域：ChatSession 对象 + 内存驻留表 + 跨会话 pending 事件 + 世界书解析。

依赖：core.paths(SESSIONS_DIR) 与 memory_store（无回环）。
sessions_map / global_pending_event 是【就地修改】的单例，绝不重新赋值，
所以各模块 import 到的是同一对象。
"""
import json
import threading
import time

from core.paths import SESSIONS_DIR
import memory_store

# 会话实际绑定的选择（只存名字）。preset 在生成时展开成 main/style/post。
SESSION_BINDING_KEYS = ["preset", "user", "character"]

# 场景闩锁创世初始值（新会话/无历史时的时空起点，由聊天 AI 在第 1 轮负责开辟时空）
GENESIS_SCENE = {"scene_id": "scene_0", "time": "未初始化", "place": "未初始化"}

# 内存中驻留的会话对象 { session_id: ChatSession }
sessions_map = {}
# claude_mode 下任意会话来新消息时触发，供 wait_pending 跨会话感知
global_pending_event = threading.Event()


class ChatSession:
    def __init__(self, session_id):
        self.session_id = session_id
        self.dir = SESSIONS_DIR / session_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.memory_file = self.dir / "memory.json"
        self.prompts_file = self.dir / "active_prompts.json"
        self.worldbooks_file = self.dir / "worldbooks.json"

        self.lock = threading.Lock()
        self.messages = []
        self.pending_event = threading.Event()
        self.pending_text = ""
        self.proactive_request = (
            None  # claude_mode 下被调度唤醒时，存待组织的主动联系事由
        )
        self.is_typing = False
        self.typing_ts = 0
        self.current_status = ""  # 新增：记录 AI 当前在干嘛（思考中/调工具等）
        # 会话绑定：只存名字。preset 默认 default，user/character 默认 default。
        self.active_prompts = {k: "default" for k in SESSION_BINDING_KEYS}
        # 本会话手动挂载的额外世界书 id 列表（角色/用户绑定的会自动并入，不存这里）
        self.active_worldbooks = []
        self.last_llm_payload = None
        self.interrupted = False
        # 场景闩锁：创世默认，随后从历史末条带戳消息恢复（重启不丢时空）
        self.current_scene_id = GENESIS_SCENE["scene_id"]
        self.last_scene_id = GENESIS_SCENE["scene_id"]
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
                self.last_scene_id = self.current_scene_id
                break

    def update_scene(self, scene_dict):
        """更新场景状态，并记录旧的 scene_id 以便检测跳转。"""
        if not scene_dict:
            return
        if scene_dict.get("scene_id"):
            self.current_scene_id = scene_dict["scene_id"]
        if scene_dict.get("time"):
            self.current_time = scene_dict["time"]
        if scene_dict.get("place"):
            self.current_place = scene_dict["place"]
    def load_messages(self):
        self.messages = memory_store.get_messages(self.session_id)
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
            self.prompts_file.write_text(
                json.dumps(self.active_prompts, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

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
                json.dumps(self.active_worldbooks, ensure_ascii=False), encoding="utf-8"
            )

    def save_messages_async(self):
        with self.lock:
            self._ensure_dir()
            memory_store.save_messages(self.session_id, self.messages)
    def read_memory(self):
        if self.memory_file.exists():
            try:
                return json.loads(self.memory_file.read_text(encoding="utf-8")).get(
                    "profile", ""
                )
            except Exception:
                pass
        return "暂时没有关于用户的特殊记忆。"

    def write_memory(self, profile_text):
        with self.lock:
            self._ensure_dir()
            data = {
                "profile": profile_text,
                "updated_at": time.strftime("%Y-%m-%d %H:%M"),
            }
            self.memory_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )


def get_session(session_id="default"):
    if not session_id:
        session_id = "default"
    session_id = "".join([c for c in session_id if c.isalnum() or c in "-_"])
    if not session_id:
        session_id = "default"

    if session_id not in sessions_map:
        sessions_map[session_id] = ChatSession(session_id)
    else:
        # 缓存命中时也要确认磁盘目录还在，否则后续写入会抛 FileNotFoundError
        sessions_map[session_id]._ensure_dir()
    return sessions_map[session_id]


def _session_scope(session):
    """记忆作用域：按会话隔离。新建会话=空记忆；克隆会话=fork 继承来源记忆。"""
    return f"sess:{session.session_id}"


def _resolve_session_worldbooks(session):
    """解析本会话适用的世界书：绑定到当前角色 + 绑定到当前用户 + 本会话手动挂载的。
    返回 (scope 列表, 命中的世界书 id 列表)。"""
    char = session.active_prompts.get("character") or "default"
    user = session.active_prompts.get("user") or "default"
    manual = set(getattr(session, "active_worldbooks", []) or [])
    ids = []
    for wb in memory_store.list_worldbooks():
        bid = wb["id"]
        bt, tgt = wb.get("bind_type"), wb.get("bind_target")
        if (
            (bt == "character" and tgt == char)
            or (bt == "user" and tgt == user)
            or (bid in manual)
        ):
            ids.append(bid)
    return [memory_store.worldbook_scope(i) for i in ids], ids


def _find_pending_session():
    """跨会话扫描：返回第一个 pending_event 已置位的会话（claude_mode 长轮询用）。"""
    for s in list(sessions_map.values()):
        if s.pending_event.is_set():
            return s
    return None
