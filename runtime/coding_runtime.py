# -*- coding: utf-8 -*-
"""
Code Agent 核心模块 (独立于 server.py)
==========================================
api-mode 自驱编码 agent：自己调 OpenAI 兼容 API 跑工具循环，在「每个任务独占的
沙箱工作区」里读写/执行/测试代码，用持久化「进度卡(checkpoint)」解决跨轮/跨 AI
实例的上下文续接，并通过共享限流器 + 子 agent 编排(map-reduce)规避 429。

  Q1 上下文管理：进度卡 + 滑动窗口（不污染角色记忆系统）。
  Q2 自测试：每任务一个真实沙箱工作区，工具 root 在此，可写代码后真正跑测试。
  Q3 进度续接：每轮强制刷新进度卡，下一轮开头注入，永远从当前进度续。
  Q4 规避 429：共享限流器；规划器(贵模型)用 explore_codebase 把侦察问题一次问完、并发派给
     子任务，并发派发给便宜高 RPM 的 worker 模型，汇总后一次性回喂规划器。

命令行单跑：  python agent.py --help
"""

import argparse
import collections
import json
import os
import re
import shutil
import sqlite3
import threading
import time
import urllib.request
from pathlib import Path

import tooling

# ---------------------------------------------------------------------------
# 路径与配置
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent  # runtime/ 的上一层 = 项目根
DATA_DIR = ROOT / "data"
AGENT_DB = DATA_DIR / "agent.db"
WORKSPACES_DIR = DATA_DIR / "agent_workspaces"
CONFIG_FILE = ROOT / "config.json"

_db_lock = threading.Lock()
_running = {}
_cancel = set()  # 装着“被请求中断”的 task_id
_cancel_lock = threading.Lock()
_queue = {}  # task_id -> [追加/补充的用户消息]，agent 工作时排队、并入下一轮
_queue_lock = threading.Lock()
_LOG_RING = collections.deque(maxlen=500)  # 调试日志环形缓冲，供前端 /api/logs 拉取
_LOG_SEQ = 0
_LOG_LOCK = threading.Lock()
_last_prompt = {}  # task_id -> 最近一次发给主模型的完整 payload（调试用）
_last_prompt_lock = threading.Lock()


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _log(*a):
    global _LOG_SEQ
    ts = time.strftime("%H:%M:%S")
    line = " ".join(str(x) for x in a)
    with _LOG_LOCK:
        _LOG_SEQ += 1
        _LOG_RING.append({"id": _LOG_SEQ, "ts": ts, "line": line})
    print(f"[{ts}][agent]", *a, flush=True)


def get_debug(after=0):
    """返回 id>after 的调试日志（供前端 vConsole 轮询）。"""
    with _LOG_LOCK:
        return [e for e in _LOG_RING if e["id"] > after]


def load_config():
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        _log("config.json 读取失败:", e)
        return {}


WS_IGNORE = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".DS_Store",
    ".idea",
    ".vscode",
    "data",
    "icons",
    ".DS_Store",
    "CLAUDE.md",
    "MEMORY_SYSTEM.md",
    "config.json",
}


def _workspace_tree(ws_path, max_depth=4, dirs_only=False):
    """生成工作区目录树文本。dirs_only=True 只列目录（给前端面板用，避免太长）。"""
    root = Path(ws_path) if ws_path else None
    if not root or not root.exists():
        return ""
    lines = []

    def walk(cur, prefix="", depth=0):
        if depth > max_depth:
            return
        try:
            items = [
                x
                for x in cur.iterdir()
                if x.name not in WS_IGNORE and not x.name.startswith(".")
            ]
            if dirs_only:
                items = [x for x in items if x.is_dir()]
            items.sort(key=lambda x: (not x.is_dir(), x.name))
        except (PermissionError, OSError):
            return
        for i, it in enumerate(items):
            last = i == len(items) - 1
            lines.append(f"{prefix}{'└── ' if last else '├── '}{it.name}")
            if it.is_dir():
                walk(it, prefix + ("    " if last else "│   "), depth + 1)

    walk(root)
    return "\n".join(lines)


def get_last_prompt(task_id):
    """返回最近一次发给主模型的完整上下文（messages），供前端调试查看。"""
    with _last_prompt_lock:
        return _last_prompt.get(task_id)


def workspace_tree(task_id):
    """对外：返回某任务工作区的【目录】树文本（供前端面板展示，只列目录避免太长）。"""
    t = get_task(task_id)
    return _workspace_tree(t["workspace"], dirs_only=True) if t else ""


# ---------------------------------------------------------------------------
# SQLite：独立的 agent.db（与角色记忆 memory.db 完全隔离）
# ---------------------------------------------------------------------------
def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(AGENT_DB))
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id          TEXT PRIMARY KEY,
            title       TEXT,
            goal        TEXT,
            status      TEXT,
            progress    INTEGER DEFAULT 0,
            workspace   TEXT,
            created_at  TEXT,
            updated_at  TEXT
        );
        CREATE TABLE IF NOT EXISTS turns (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id   TEXT,
            role      TEXT,
            type      TEXT,
            content   TEXT,
            tool_name TEXT,
            ts        TEXT
        );
        CREATE TABLE IF NOT EXISTS checkpoints (
            task_id   TEXT PRIMARY KEY,
            version   INTEGER DEFAULT 0,
            card      TEXT,
            ts        TEXT
        );
        CREATE TABLE IF NOT EXISTS context_cache (
            task_id   TEXT,
            filepath  TEXT,
            mode      TEXT DEFAULT 'outline',
            ts        TEXT,
            PRIMARY KEY (task_id, filepath)
        );
        CREATE INDEX IF NOT EXISTS idx_turns_task ON turns(task_id, id);
        """
    )
    try:
        con.execute("ALTER TABLE tasks ADD COLUMN confirmed INTEGER DEFAULT 0")
    except Exception:
        pass  # 列已存在
    con.commit()
    con.close()


def _conn():
    con = sqlite3.connect(str(AGENT_DB))
    con.row_factory = sqlite3.Row
    return con


# ---- tasks ----------------------------------------------------------------
def create_task(title, goal="", seed_dir=None, work_dir=None):
    tid = "task_" + str(int(time.time() * 1000))
    if work_dir and Path(work_dir).is_dir():
        # 直接在原目录工作：不拷贝，agent 改的就是原文件（请自行备份/用 git）
        ws = Path(work_dir).resolve()
        _log(f"任务 {tid} 直接在原目录工作(in-place): {ws}")
    else:
        ws = (WORKSPACES_DIR / tid).resolve()
        ws.mkdir(parents=True, exist_ok=True)
        if work_dir:
            _log(f"work_dir 无效，改用空白沙箱: {work_dir}")
        # 可选：把一个现有项目“种”进沙箱，让 agent 在隔离副本上改
        if seed_dir:
            src = Path(seed_dir)
            if src.exists() and src.is_dir():
                try:
                    shutil.copytree(
                        src,
                        ws,
                        dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(
                            ".git",
                            "node_modules",
                            "__pycache__",
                            ".venv",
                            "venv",
                            ".DS_Store",
                            "*.pyc",
                        ),
                    )
                    _log(f"已把现有项目种入沙箱: {src} -> {ws}")
                except Exception as ex:
                    _log(f"种项目失败: {ex}")
            else:
                _log(f"seed_dir 不存在或非目录，跳过: {seed_dir}")
    with _db_lock, _conn() as con:
        con.execute(
            "INSERT INTO tasks(id,title,goal,status,progress,workspace,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (tid, title, goal or title, "就绪", 0, str(ws), _now(), _now()),
        )
    _log(f"新建任务 {tid} 工作区={ws}")
    return get_task(tid)


def get_task(task_id):
    with _conn() as con:
        r = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(r) if r else None


def list_tasks():
    with _conn() as con:
        rows = con.execute("SELECT * FROM tasks ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]


def update_task(task_id, **fields):
    if not fields:
        return
    fields["updated_at"] = _now()
    cols = ", ".join(f"{k}=?" for k in fields)
    with _db_lock, _conn() as con:
        con.execute(f"UPDATE tasks SET {cols} WHERE id=?", (*fields.values(), task_id))


def delete_task(task_id):
    with _db_lock, _conn() as con:
        con.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        con.execute("DELETE FROM turns WHERE task_id=?", (task_id,))
        con.execute("DELETE FROM checkpoints WHERE task_id=?", (task_id,))
        con.execute("DELETE FROM context_cache WHERE task_id=?", (task_id,))


# ---- turns（完整事件流，供前端回放） --------------------------------------
def add_turn(task_id, role, ttype, content, tool_name=None):
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)
    with _db_lock, _conn() as con:
        cur = con.execute(
            "INSERT INTO turns(task_id,role,type,content,tool_name,ts) VALUES(?,?,?,?,?,?)",
            (task_id, role, ttype, content, tool_name, _now()),
        )
        return cur.lastrowid


def get_turns(task_id, after_id=0):
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM turns WHERE task_id=? AND id>? ORDER BY id",
            (task_id, after_id),
        ).fetchall()
        return [dict(r) for r in rows]


# ---- checkpoints（进度卡，Q1 + Q3 的核心） --------------------------------
def get_checkpoint(task_id):
    with _conn() as con:
        r = con.execute(
            "SELECT * FROM checkpoints WHERE task_id=?", (task_id,)
        ).fetchone()
        if not r:
            return None
        try:
            card = json.loads(r["card"])
        except Exception:
            card = {}
        return {"version": r["version"], "card": card, "ts": r["ts"]}


def save_checkpoint(task_id, card, version):
    payload = json.dumps(card, ensure_ascii=False)
    with _db_lock, _conn() as con:
        con.execute(
            "INSERT INTO checkpoints(task_id,version,card,ts) VALUES(?,?,?,?)"
            " ON CONFLICT(task_id) DO UPDATE SET version=?,card=?,ts=?",
            (task_id, version, payload, _now(), version, payload, _now()),
        )


# ---- 固定上下文缓存（钉住的参考文件，长期注入，不随聊天滚动） ----
def list_context(task_id):
    with _conn() as con:
        rows = con.execute(
            "SELECT filepath, mode, ts FROM context_cache WHERE task_id=? ORDER BY ts",
            (task_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def add_context(task_id, filepath, mode="outline"):
    if not filepath:
        return {"ok": False, "error": "filepath 必填"}
    mode = "full" if mode == "full" else "outline"
    with _db_lock, _conn() as con:
        con.execute(
            "INSERT INTO context_cache(task_id,filepath,mode,ts) VALUES(?,?,?,?)"
            " ON CONFLICT(task_id,filepath) DO UPDATE SET mode=?,ts=?",
            (task_id, filepath, mode, _now(), mode, _now()),
        )
    _log(f"📌 钉住上下文[{task_id}] {filepath} ({mode})")
    return {"ok": True, "filepath": filepath, "mode": mode}


def remove_context(task_id, filepath):
    with _db_lock, _conn() as con:
        con.execute(
            "DELETE FROM context_cache WHERE task_id=? AND filepath=?",
            (task_id, filepath),
        )
    _log(f"📌 移除上下文[{task_id}] {filepath}")
    return {"ok": True}


def list_workspace_files(task_id, limit=800):
    """列举任务工作区里的文件相对路径（给前端 Add Path 选择用）。"""
    t = get_task(task_id)
    if not t:
        return []
    root = Path(t["workspace"]).resolve()
    if not root.exists():
        return []
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames if d not in WS_IGNORE and not d.startswith(".")
        ]
        for fn in sorted(filenames):
            if fn.startswith("."):
                continue
            rel = str(Path(dirpath, fn).relative_to(root)).replace("\\", "/")
            out.append(rel)
            if len(out) >= limit:
                return sorted(out)
    return sorted(out)


def _read_worklog(task, limit=8000):
    """读取工作区 work_log.md（若有），用于每轮注入核对计划。"""
    try:
        f = Path(task["workspace"]) / "work_log.md"
        if f.exists() and f.is_file():
            return f.read_text(encoding="utf-8")[:limit]
    except Exception:
        pass
    return ""


def _render_pinned_context(task):
    """把钉住的文件渲染成注入文本：outline 只给结构，full 给带行号全码。"""
    entries = list_context(task["id"])
    if not entries:
        return ""
    ctx = _sandbox_context(task)
    blocks = []
    for e in entries:
        fp, mode = e["filepath"], e.get("mode", "outline")
        if mode == "full":
            r = tooling.execute_tool("read_file_with_lines", {"filepath": fp}, ctx)
            if r.get("error"):
                body = f"(读取失败: {r['error']})"
            else:
                content = r.get("content", "") or ""
                if len(content) > 200000:
                    content = content[:200000] + (
                        "\n...[全代码过长已截断，其余部分请用 read_file_with_lines 指定行号读取]"
                    )
                body = content
            blocks.append(f"### {fp} (全代码)\n{body}")
        else:
            r = tooling.execute_tool("get_outline", {"filepath": fp}, ctx)
            if r.get("error"):
                body = f"(大纲失败: {r['error']})"
            else:
                body = (
                    "\n".join(
                        f"- [{x['type']}] {x['name']}  L{x['start_line']}-{x['end_line']}"
                        + (f"  // {x['doc']}" if x.get("doc") else "")
                        for x in r.get("symbols", [])
                    )
                    or "(无符号)"
                )
            blocks.append(f"### {fp} (大纲)\n{body}")
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# LLM 调用 + 共享限流器
# ---------------------------------------------------------------------------
def _http_post(url, payload, api_key, timeout=120):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# 并发不等于无限放开：worker 再多，对同一端点的实际请求也被限到 RPM 以内，
# 否则“一次并发 call 很多次”只会让 429 更严重。按 endpoint 分别限流，
# 贵模型(api)与便宜高 RPM 的 worker_api 各走各的额度池、互不挤占。
DEFAULT_RPM = {"api": 5, "summary_api": 10, "worker_api": 20}


class RateLimiter:
    def __init__(self):
        self._win = {}
        self._lock = threading.Lock()

    def acquire(self, key, rpm):
        rpm = max(1, int(rpm or 5))
        while True:
            with self._lock:
                now = time.time()
                q = [t for t in self._win.get(key, []) if now - t < 61.0]
                if len(q) < rpm:
                    q.append(now)
                    self._win[key] = q
                    return
                wait = 61.0 - (now - q[0]) + 0.5
                self._win[key] = q
            _log(f"[限流 {key}] 已达 {rpm} RPM，等待 {wait:.1f}s")
            time.sleep(max(0.1, wait))


_rate_limiter = RateLimiter()


def _resolve_api(cfg_key):
    """解析逻辑端点 -> (限流桶key, rpm, 端点配置)。
    限流桶与 rpm 跟着【逻辑端点】走：worker_api 永远走自己的桶和 rpm，
    即便实际请求端点回退到 flash（summary_api）或主模型，也不混进别人的额度池。"""
    cfg_all = load_config()
    if cfg_key == "worker_api":
        wk = cfg_all.get("worker_api") or {}
        rpm = wk.get("rpm") or DEFAULT_RPM["worker_api"]
        endpoint = wk if (wk.get("base_url") and wk.get("api_key")) else {}
        if not endpoint:
            sm = cfg_all.get("summary_api") or {}
            endpoint = (
                sm
                if (sm.get("base_url") and sm.get("api_key"))
                else (cfg_all.get("api", {}) or {})
            )
        return "worker_api", rpm, endpoint
    endpoint = cfg_all.get(cfg_key, {}) or {}
    rpm = endpoint.get("rpm") or DEFAULT_RPM.get(cfg_key, 5)
    return cfg_key, rpm, endpoint


def _chat(cfg_key, messages, tools=None, temperature=0.3, timeout=120):
    """发一次 chat completion，全程经共享限流器。"""
    rl_key, rpm, cfg = _resolve_api(cfg_key)
    base = (cfg.get("base_url") or "").rstrip("/")
    url = f"{base}/chat/completions"
    payload = {
        "model": cfg.get("model", "gpt-4o-mini"),
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    _rate_limiter.acquire(rl_key, rpm)
    _log(
        f"↗ LLM {rl_key}/{payload['model']} ctx={len(messages)} tools={'Y' if tools else 'N'} (rpm{rpm})"
    )
    return _http_post(url, payload, cfg.get("api_key", ""), timeout)


# ---------------------------------------------------------------------------
# 沙箱：每任务的编码工具 root 在它自己的工作区目录里
# ---------------------------------------------------------------------------
def _enforce_workflow():
    return bool((load_config().get("agent", {}) or {}).get("enforce_workflow", True))


def _auto_venv():
    return bool((load_config().get("agent", {}) or {}).get("auto_venv", True))


def _venv_paths(ws):
    venv_dir = Path(ws) / ".venv"
    bindir = venv_dir / ("Scripts" if os.name == "nt" else "bin")
    py = bindir / ("python.exe" if os.name == "nt" else "python")
    return venv_dir, bindir, py


def _is_py_cmd(cmd):
    """命令是否涉及 Python（python/pip/pytest/py），用于按需触发建 venv。"""
    return bool(re.search(r"(?:^|[\s;&|(])(python3?|pip3?|pytest|py)\b", cmd or ""))


def _venv_ensure(task):
    """按需为任务工作区创建 .venv（已存在则跳过）。"""
    if not _auto_venv():
        return
    import subprocess
    import sys

    ws = Path(task["workspace"]).resolve()
    venv_dir, _bindir, py = _venv_paths(ws)
    if py.exists():
        return
    try:
        _log(f"为任务创建专属 venv: {venv_dir}")
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            cwd=str(ws),
            capture_output=True,
            timeout=180,
        )
    except Exception as ex:
        _log(f"创建 venv 失败: {ex}")


def _sandbox_context(task):
    ws = Path(task["workspace"]).resolve()
    ws.mkdir(parents=True, exist_ok=True)

    def safe_resolve(rel_path):
        target = (ws / str(rel_path)).resolve()
        if not str(target).startswith(str(ws)):
            raise ValueError(f"安全拦截：拒绝访问沙箱外路径 ({rel_path})")
        return target

    ctx = {
        "root_dir": ws,
        "prompts_dir": ws,
        "sessions_dir": ws,
        "safe_resolve_cb": safe_resolve,
        "get_session_cb": lambda *a, **k: None,
        "memory_store": None,
        "embed_cb": lambda *a, **k: None,
    }
    if _auto_venv():
        # 把任务专属 venv 的 bin 目录放到 PATH 最前，python/pip/pytest 自动走 venv。
        venv_dir, bindir, _py = _venv_paths(ws)
        ctx["env"] = {
            "VIRTUAL_ENV": str(venv_dir),
            "PATH": str(bindir) + os.pathsep + os.environ.get("PATH", ""),
        }
    return ctx


# ---------------------------------------------------------------------------
# 系统提示词
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """你是「本地代码副驾驶 (Coding Agent)」，自主运行在一个隔离的沙箱工作区里。
你能读写/执行/测试工作区里的代码。目标：以可验证、低返工的方式把开发任务做完做对。

【项目背景与技术栈】（非常重要）：
这是一个本地 AI 聊天桥接项目（Chat Bridge）。
- 后端：Python 3 (主要是 server.py 和各种工具/记忆管理脚本)
- 前端：原生 HTML + CSS + JavaScript (位于 frontend 目录，无 React/Vue 等重型框架)
- 数据库：SQLite (位于 data 目录下的 jobs.db 等)

可用工具：
- read_file_with_lines（可传 offset/limit 只读一段，大文件友好）
- glob_files（按通配符列文件/看结构）
- grep_files、get_outline（看文件结构大纲：类/函数/方法+行号）
- get_function_code（取某函数的完整带行号代码）
- replace_in_file（按内容精确替换、改文件优先用它）
- apply_file_edits（按行号批量改）
- batch_write_files（新建/覆盖文件）
- run_terminal_command（可传 timeout 秒数，装包/构建/测试可调大；Python 命令会自动在本任务专属 venv 里跑，pip 装包只进 venv、不污染全局）
- explore_codebase（一次问多个问题，并发只读子 agent 帮你侦察代码、回带行号引用的答案）。

所有路径相对工作区根目录，系统每轮会在上下文里给你刷新【工作区文件树】。
你可以在一条消息里【同时发多个只读工具调用】（read/grep/glob/get_outline/get_function_code），server 会并发执行并把结果一次性都给你。

━━ 效率第一（最重要）━━
- 目标是用【最少的往返次数】把活做对。每次工具调用都要等限流，几十次往返会非常慢——能一次读全就绝不一点点抠。
- 任务涉及的文件不多时：直接用 read_file_with_lines 把相关文件【整文件读进来】（同一条消息里可并发读多个），
  然后一次性想清楚、用 apply_file_edits / replace_in_file 把所有改动一次做完，再跑一次验证。
- 不要逐行 grep、不要 outline、不要 explore_codebase 去"省 token"——那些只为【超大型陌生代码库】准备，
  对几百行的中小项目纯属拖慢。你的上下文预算很宽，不缺 token，缺的是【调用次数和时间】，宁可一次多读点。
- 用户在固定上下文里钉了文件(尤其"全代码"模式)，就直接用那份内容，别再去重复 read/grep 抓一遍。

━━ 第 1 轮：先跟用户确认需求（系统这一轮只给你只读工具）━━
收到新任务的第一条消息时，这一轮【可只读看代码，但只确认、不动手】：
1) 用你自己的话复述需求：目标 / 范围 / 明确不做什么 / 已知约束。
2) 列出你不确定、需要用户拍板的问题（实现方式、影响范围、边界情况等）。
3) 收尾请用户确认或补充，然后停下等回复——这一轮可以只读看代码，但绝不改代码/跑命令/落盘。
（宁可多问一句，也别猜错了瞎干导致返工。用户确认后再进入下面的规划轮。）

━━ 第 2 轮起：先规划再开干 ━━
用户确认需求后：
1) 把计划写进 work_log.md（有序 Todo，每步注明「目的·影响文件·验证方式」）。【小任务从简】，
   一两行 Todo 即可，复杂任务才详细拆。没有 work_log.md 之前系统会拦住你改代码。
2) 选方案：若有多种实现，简述采用哪个及理由。

━━ 后续轮次：增量开发 + 立即验证 ━━
- 一次只推进一个 Todo，别同时改多个不相关模块，别留「稍后再修」。
- 每改完一处，立刻用 run_terminal_command 验证（优先级：单元测试 > 类型检查 > lint >
  构建 > 最小运行）。失败就读报错→定位→修复→重新验证，直到通过，再勾掉该 Todo。
- 能批量就别拆多轮（apply_file_edits 一次多处、batch_write_files 一次多文件），减少往返、省额度。
- 默认就【直接读相关文件全文】再动手改；只有面对超大型陌生代码库、确实要跨很多文件摸清时，
  才考虑 explore_codebase / get_outline 这类省 token 手段。中小型任务用它们只会更慢。

━━ 纪律 ━━
- 【环境限制】你所在的沙箱终端是 Windows 环境。绝对禁止在 run_terminal_command 中使用 grep, cat, ls, find 等 Linux 命令查找文件。
- 每轮调用工具前用一两句话说明：这一轮做了什么、有什么错误需要避免、当前进度、下一步等指导后续工作的内容。
- 绝不输出未经验证的结论，绝不「理论上应该没问题」就声称完成。
- 调用工具一律用原生 function calling（结构化 tool_calls），【绝不要把工具调用的 JSON 或
  <tool_call> 之类的格式写进你的正文里】——否则你正文里那段可能被上游解析器吃掉、导致前面的话丢失。
- 全部 Todo 完成且通过整体验证后，给出交付说明（完成内容/改动文件/验证结果/已知限制），
  并在回复中明确写出 [TASK_DONE]。
"""


def _build_messages(task, checkpoint, recent_turns, user_msg):
    """系统提示 + 最初需求 + 进度卡(始终注入,Q3) + 最近 N 轮原文(滑动窗口,Q1) + 新输入。"""
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    msgs.append({"role": "system", "content": f"【任务目标】\n{task.get('goal', '')}"})
    tree = _workspace_tree(task.get("workspace"))
    msgs.append(
        {
            "role": "system",
            "content": "【工作区文件树（沙箱根目录，每轮刷新）】\n"
            + (tree or "(空：还没有任何文件)"),
        }
    )
    has_wl = bool(wl_path := (Path(task["workspace"]) / "work_log.md")) and wl_path.exists()
    phase = (
        "执行中（已有 work_log.md，按计划推进、每改一处立即验证、勾掉已完成 Todo）"
        if has_wl
        else "规划中（尚无 work_log.md —— 本轮先复述需求 + 写有序 Todo 落盘到 work_log.md，先别改代码/跑命令）"
    )
    msgs.append({"role": "system", "content": f"【当前阶段】{phase}"})
    wl = _read_worklog(task)
    if wl:
        msgs.append(
            {
                "role": "system",
                "content": "【work_log.md（你的规划/Todo，每轮都要核对并按它推进；勾掉已完成项）】\n"
                + wl,
            }
        )
    pinned = _render_pinned_context(task)
    if pinned:
        msgs.append(
            {
                "role": "system",
                "content": "【固定上下文（已钉住的参考文件，长期保留，每轮刷新）】\n"
                "可用 pin_file/unpin_file 自行增减。\n\n" + pinned,
            }
        )
    if checkpoint and checkpoint.get("card"):
        msgs.append(
            {
                "role": "system",
                "content": "【当前进度卡】（任务到目前为止的状态，从这里续，不要从零重读）\n"
                + json.dumps(checkpoint["card"], ensure_ascii=False, indent=2),
            }
        )
    for t in recent_turns:
        if t["type"] == "text" and t["role"] in ("user", "assistant"):
            msgs.append({"role": t["role"], "content": t["content"]})
        elif t["type"] == "tool_result":
            msgs.append(
                {
                    "role": "system",
                    "content": f"[工具 {t['tool_name']} 返回] {t['content'][:1500]}",
                }
            )
    if user_msg:
        msgs.append({"role": "user", "content": user_msg})
    return msgs


# ---------------------------------------------------------------------------
# 进度卡更新（Q1 压缩器 + Q3 续接，强制每轮执行）
# ---------------------------------------------------------------------------
def update_checkpoint(task, new_turns_text):
    prev = get_checkpoint(task["id"])
    prev_card = prev["card"] if prev else {}
    version = (prev["version"] if prev else 0) + 1

    instruction = (
        "你是任务进度记录员。根据【旧进度卡】和【本轮新发生的事】，输出更新后的进度卡。"
        "只输出 JSON，字段固定："
        '{"summary":"一句话总览","done":["已完成"],"todo":["待办"],'
        '"files":["改动的文件"],"open_questions":["未决"],'
        '"notes":["避坑指南/环境限制/试错经验(永久保留)"],'
        '"last_action":"最近一步","progress":0到100整数,"status":"运行中/等待输入/已完成/失败"}。'
        "合并而非覆盖：保留旧卡里仍成立的信息。"
    )
    messages = [
        {"role": "system", "content": instruction},
        {
            "role": "user",
            "content": "【旧进度卡】\n"
            + json.dumps(prev_card, ensure_ascii=False)
            + "\n\n【本轮新发生的事】\n"
            + new_turns_text[:6000],
        },
    ]
    card = None
    try:
        res = _chat("summary_api", messages, temperature=0.1, timeout=60)
        raw = res["choices"][0]["message"].get("content", "") or ""
        s, e = raw.find("{"), raw.rfind("}")
        if s >= 0 and e > s:
            card = json.loads(raw[s : e + 1])
    except Exception as ex:
        _log("进度卡 LLM 更新失败，走兜底:", ex)

    if not isinstance(card, dict):
        card = dict(prev_card)
        card.setdefault("done", [])
        card["last_action"] = new_turns_text.strip()[-300:]
        card.setdefault("summary", task.get("title", ""))
        card.setdefault("status", "运行中")
        card.setdefault("progress", prev_card.get("progress", 10))

    save_checkpoint(task["id"], card, version)
    _log(
        f"📌 进度卡 v{version} {card.get('progress')}% {card.get('status')} | {str(card.get('summary'))[:40]}"
    )
    return card


# ---------------------------------------------------------------------------
# 子 agent 编排（map-reduce）：规划器拆只读子任务 -> 并发 worker -> 汇总回喂
# ---------------------------------------------------------------------------
WORKER_TOOL_NAMES = {
    "read_file_with_lines",
    "grep_files",
    "glob_files",
    "get_outline",
    "get_function_code",
}
# 只读工具：同一批里全是这些时可并发执行（写/执行类不并发，避免竞态）
READONLY_TOOL_NAMES = set(WORKER_TOOL_NAMES)
WORKER_MAX_ROUNDS = (
    6  # worker 要决断：几次工具内给结论。安全阀，真正速率由 RPM 滑窗控制
)
DEFAULT_WORKER_CONCURRENCY = (
    3  # 别一次起太多 worker：既防 flash 端点过载，也别瞬间打爆 RPM
)

WORKER_SYSTEM_PROMPT = (
    "你是一个只读代码研究员，必须【高效、决断】。可用 get_outline / grep_files / glob_files / "
    "read_file_with_lines（只读，严禁写入或执行命令）。"
    "你最多只有约 6 次工具调用：优先 grep/outline 快速定位，1~3 次工具内就给出结论，"
    "绝不逐个文件通读、绝不无止境地查。即便信息不全，也要基于已查到的给出最相关发现 + 来源"
    "（文件名:行号）。只回答分配给你的这一个问题，简洁。"
)


def get_worker_tools():
    return [
        t
        for t in tooling.get_coding_tools()
        if t.get("function", {}).get("name") in WORKER_TOOL_NAMES
    ]


def get_orchestration_tools():
    return [
        {
            "type": "function",
            "function": {
                "name": "explore_codebase",
                "description": (
                    "【侦察/理解代码优先用它】一次性传入一个或多个问题，server 会并发派便宜高 RPM 的"
                    "只读子 agent 去翻代码，汇总成带『文件:行号』引用的答案一次性返回给你。把'读懂现有"
                    "结构/定位实现/排查多个点'这类侦察活儿尽量一次问完——不管几个问题都只占你一次调用，"
                    "省额度、规避 429。它只读不改；拿到答案后你再自己读那几个关键位置并动手改。注意：每个问题都会起一个便宜模型的子 agent，适合【跨多文件/多区域的并行侦察】；若只是简单定位一两处，直接用 get_outline/grep_files 自己查更快，别为此动用 explore。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "questions": {
                            "type": "array",
                            "description": "要弄清的问题列表（尽量一次把侦察问题都列上，别一个个分开问）",
                            "items": {"type": "string"},
                        },
                        "max_concurrency": {
                            "type": "integer",
                            "description": f"最大并发，默认 {DEFAULT_WORKER_CONCURRENCY}",
                        },
                    },
                    "required": ["questions"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "pin_file",
                "description": (
                    "把一个文件【钉进固定上下文】：之后每一轮都会自动把它给你看，不随聊天滚动被挤掉。"
                    "mode='outline' 只给结构大纲(省 token，适合大文件/只需结构)；mode='full' 给带行号全代码"
                    "(适合反复要改/通读的小文件)。用于你需要长期参考的文件，省得反复 read。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filepath": {
                            "type": "string",
                            "description": "相对工作区根的文件路径",
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["outline", "full"],
                            "description": "outline=只看结构(默认) / full=看全代码",
                        },
                    },
                    "required": ["filepath"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "unpin_file",
                "description": "从固定上下文移除一个之前钉住的文件（不再每轮注入）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filepath": {
                            "type": "string",
                            "description": "要移除的文件路径",
                        }
                    },
                    "required": ["filepath"],
                },
            },
        },
    ]


def run_worker(task, subtask, ctx):
    """跑一个只读 worker 子 agent（worker_api 模型 + 只读工具）。"""
    sid = subtask.get("id") or ""
    instruction = subtask.get("instruction") or ""
    msgs = [
        {"role": "system", "content": WORKER_SYSTEM_PROMPT},
        {"role": "system", "content": f"【总任务背景】{task.get('goal', '')}"},
        {"role": "user", "content": instruction},
    ]
    tools = get_worker_tools()
    try:
        for _ in range(WORKER_MAX_ROUNDS):
            res = _chat("worker_api", msgs, tools=tools, temperature=0.2, timeout=90)
            choice = res["choices"][0]["message"]
            tcs = choice.get("tool_calls") or []
            if not tcs:
                return {
                    "id": sid,
                    "instruction": instruction,
                    "result": (choice.get("content") or "").strip(),
                    "ok": True,
                }
            msgs.append(
                {
                    "role": "assistant",
                    "content": choice.get("content") or "",
                    "tool_calls": tcs,
                }
            )
            for tc in tcs:
                fn = tc.get("function", {}) or {}
                fname = fn.get("name", "")
                try:
                    fargs = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    fargs = {}
                _log(
                    f"  ↪ worker[{sid}] {fname} {json.dumps(fargs, ensure_ascii=False)[:50]}"
                )
                if fname in WORKER_TOOL_NAMES:
                    r = tooling.execute_tool(fname, fargs, ctx)
                else:
                    r = {"error": f"只读 worker 不允许工具: {fname}"}
                msgs.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id"),
                        "content": json.dumps(r, ensure_ascii=False),
                    }
                )
        return {
            "id": sid,
            "instruction": instruction,
            "result": "(worker 达最大轮数仍未给出结论)",
            "ok": False,
        }
    except Exception as e:
        return {
            "id": sid,
            "instruction": instruction,
            "result": f"worker 异常: {e}",
            "ok": False,
        }


def run_explore(task, args, emit=None):
    """explore_codebase 入口：把 questions 列表映射成并发只读子任务，复用 worker 引擎。"""
    qs = args.get("questions") or []
    if isinstance(qs, str):
        qs = [qs]
    subtasks = [
        {"id": f"q{i + 1}", "instruction": q}
        for i, q in enumerate(qs)
        if isinstance(q, str) and q.strip()
    ]
    if not subtasks:
        return {"error": "questions 必须是非空字符串数组"}
    return run_spawn_subagents(
        task,
        {"subtasks": subtasks, "max_concurrency": args.get("max_concurrency")},
        emit=emit,
    )


def run_spawn_subagents(task, args, emit=None):
    """并发跑一批只读 worker，汇总成一个包返回（给规划器的那一次 tool 结果）。"""
    import concurrent.futures as cf

    subtasks = args.get("subtasks") or []
    if not isinstance(subtasks, list) or not subtasks:
        return {"error": "subtasks 必须是非空数组"}
    max_conc = max(1, int(args.get("max_concurrency") or DEFAULT_WORKER_CONCURRENCY))
    ctx = _sandbox_context(task)
    results = [None] * len(subtasks)

    _log(f"🚀 fanout {len(subtasks)} 个子 agent (并发上限 {max_conc})")
    if emit:
        emit("subagents_start", {"count": len(subtasks), "max_concurrency": max_conc})

    def _run_one(st):
        s0 = time.time()
        r = run_worker(task, st, ctx)
        el = round(time.time() - s0, 2)
        if isinstance(r, dict):
            r["elapsed"] = el
        _id = r.get("id") if isinstance(r, dict) else "?"
        _ok = "ok" if (isinstance(r, dict) and r.get("ok")) else "fail"
        _log(f"✔ worker[{_id}] {_ok} {el}s")
        return r

    with cf.ThreadPoolExecutor(max_workers=max_conc) as ex:
        futs = {ex.submit(_run_one, st): i for i, st in enumerate(subtasks)}
        for f in cf.as_completed(futs):
            i = futs[f]
            results[i] = f.result()
            if emit:
                emit("subagent_done", results[i])

    return {"ok": True, "count": len(results), "results": results}


# ---------------------------------------------------------------------------
# 主循环：跑一个 agent 回合
# ---------------------------------------------------------------------------
MAX_TOOL_ROUNDS = 99
RECENT_TURNS = 100


def run_agent_turn(task_id, user_msg, on_event=None):
    """完整回合：从 user_msg 出发跑工具循环直到最终文本；事件全落库；结束强制刷新进度卡。"""
    task = get_task(task_id)
    if not task:
        raise ValueError(f"任务不存在: {task_id}")
    if _running.get(task_id):
        raise RuntimeError("该任务正在运行中")
    _running[task_id] = True
    with _cancel_lock:
        _cancel.discard(task_id)

    def emit(kind, data):
        if on_event:
            try:
                on_event(kind, data)
            except Exception:
                pass

    try:
        update_task(task_id, status="运行中")
        # 需求确认闸门：新任务的第一条消息 = 确认轮（只复述+提问+停下等用户确认，不动手）
        _prior_users = sum(
            1 for t in get_turns(task_id)
            if t["type"] == "text" and t["role"] == "user"
        )
        if user_msg:
            add_turn(task_id, "user", "text", user_msg)
            emit("user", user_msg)

        confirm_round = False
        if _enforce_workflow() and not task.get("confirmed"):
            if _prior_users == 0:
                confirm_round = True  # 第一条消息：先复述确认
            else:
                update_task(task_id, confirmed=1)  # 用户已回复 -> 确认通过
                task["confirmed"] = 1

        checkpoint = get_checkpoint(task_id)
        # 智能选取上下文：保留最近若干「对话文本」+ 最近若干「工具结果」，避免工具往返
        # 把最初的用户消息挤出窗口；user_msg 上面已落库，这里不再单独重复追加（否则会重复两遍）。
        _all = get_turns(task_id)
        _texts = [
            t for t in _all
            if t["type"] == "text" and t["role"] in ("user", "assistant")
        ]
        _toolres = [t for t in _all if t["type"] == "tool_result"]
        recent = sorted(
            _texts[-RECENT_TURNS:] + _toolres[-8:], key=lambda t: t["id"]
        )
        # 硬留住「最初的用户需求」——它绝不能被滑动窗口挤掉，否则 agent 会忘了在做什么
        _first_user = next(
            (t for t in _all if t["type"] == "text" and t["role"] == "user"), None
        )
        if _first_user and _first_user["id"] not in {t["id"] for t in recent}:
            recent = [_first_user] + recent
        api_messages = _build_messages(task, checkpoint, recent, None)

        ctx = _sandbox_context(task)
        tools = tooling.get_coding_tools() + get_orchestration_tools()
        if confirm_round:
            # 确认轮只给【只读】工具：能看文件弄清现状好提问，但不能改/不能跑/不能落盘
            tools = [
                t
                for t in tooling.get_coding_tools()
                if t.get("function", {}).get("name") in READONLY_TOOL_NAMES
            ]
            api_messages.append(
                {
                    "role": "system",
                    "content": "【本轮 = 需求确认轮】你只有【只读】工具（read_file_with_lines/grep_files/"
                    "glob_files/get_outline/get_function_code），可以读代码弄清现状，但本轮【不能改代码、"
                    "不能跑命令、不能写 work_log 规划】。看明白后做三件事：①用自己的话复述需求（目标/范围/"
                    "明确不做什么/约束）；②列出需要用户拍板的疑问（没有就说没有）；③收尾请用户确认或补充，"
                    "然后停下等回复。用户确认后，下一轮再规划与动手。",
                }
            )
            _log("🛑 需求确认轮：可只读看代码，但只复述+提问，等用户确认")

        new_turns_log = []
        if user_msg:
            new_turns_log.append(f"[用户] {user_msg}")

        final_text = ""
        interrupted = False
        _empty_reject = 0
        for _round in range(MAX_TOOL_ROUNDS):
            if _check_cancel(task_id):
                interrupted = True
                break
            injected = _drain_queue(task_id)
            if injected:
                note = "【用户追加/修改需求，请纳入考虑】\n" + "\n".join(injected)
                api_messages.append({"role": "user", "content": note})
                new_turns_log.append("[用户追加] " + " | ".join(injected))
                _log(f"📥 队列注入[{task_id}] {len(injected)} 条 -> 本轮上下文")
            with _last_prompt_lock:
                _last_prompt[task_id] = {
                    "ts": _now(),
                    "round": _round,
                    "model": (load_config().get("api", {}) or {}).get("model", ""),
                    "messages": list(api_messages),
                }
            res = _chat("api", api_messages, tools=tools, temperature=0.3)
            choice = res["choices"][0]["message"]
            tcs = choice.get("tool_calls") or []

            thought = choice.get("reasoning_content")
            if thought:
                add_turn(task_id, "assistant", "reasoning", thought)
                emit("reasoning", thought)

            content = (choice.get("content") or "").strip()
            if content and tcs:
                add_turn(task_id, "assistant", "text", content)
                emit("assistant", content)
                new_turns_log.append(f"[助手] {content}")

            if not tcs:
                final_text = content
                break
            if _enforce_workflow() and not content and _empty_reject < 2:
                _empty_reject += 1
                api_messages.append(
                    {"role": "assistant", "content": "", "tool_calls": tcs}
                )
                for tc in tcs:
                    api_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.get("id"),
                            "content": "【已拒绝执行】调用工具前必须先在正文(content)里用一句话说明："
                            "这一步做什么、为什么、当前进度。请重新输出本轮：先写说明，再调用工具。",
                        }
                    )
                _log("⛔ 空 content 调工具，已驳回，要求先说明再调用")
                emit("assistant", "（系统拦截：请先说明再调用工具）")
                continue
            api_messages.append(
                {
                    "role": "assistant",
                    "content": choice.get("content") or "",
                    "tool_calls": tcs,
                }
            )

            # 1) 解析并记录所有工具调用
            parsed_calls = []
            for tc in tcs:
                fn = tc.get("function", {}) or {}
                fname = fn.get("name", "")
                try:
                    fargs = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    fargs = {}
                parsed_calls.append((tc, fname, fargs))
                add_turn(
                    task_id,
                    "assistant",
                    "tool_call",
                    {"name": fname, "args": fargs},
                    tool_name=fname,
                )
                emit("tool_call", {"name": fname, "args": fargs})
                _log(f"⚙ tool {fname} {json.dumps(fargs, ensure_ascii=False)[:60]}")

            def _exec_one(call):
                _tc, _fname, _fargs = call
                if _fname == "explore_codebase":
                    return run_explore(task, _fargs, emit=lambda k, d: emit(k, d))
                if _fname == "pin_file":
                    return add_context(
                        task_id, _fargs.get("filepath"), _fargs.get("mode", "outline")
                    )
                if _fname == "unpin_file":
                    return remove_context(task_id, _fargs.get("filepath"))
                if _fname in tooling.CODING_TOOL_NAMES:
                    if _enforce_workflow() and _fname in (
                        "apply_file_edits",
                        "batch_write_files",
                        "replace_in_file",
                        "run_terminal_command",
                    ):
                        wl_exists = (Path(task["workspace"]) / "work_log.md").exists()
                        writes_wl = _fname == "batch_write_files" and any(
                            (ff.get("filepath") or "").replace("\\", "/").endswith("work_log.md")
                            for ff in (_fargs.get("files") or [])
                        )
                        if not wl_exists and not writes_wl:
                            return {
                                "error": "【规划优先·已拦截】当前还没有 work_log.md。请先把本次任务的"
                                "规划（复述需求 + 有序 Todo，每步注明目的/影响文件/验证方式）写进 "
                                "work_log.md，再开始改代码或跑命令。"
                            }
                    if _fname == "run_terminal_command" and _is_py_cmd(
                        _fargs.get("command", "")
                    ):
                        _venv_ensure(task)
                    return tooling.execute_tool(_fname, _fargs, ctx)
                return {"error": f"该 agent 不支持工具: {_fname}"}

            # 2) 同一批全是只读工具且不止一个 → 并发；否则顺序（写/执行类避免竞态）
            all_ro = len(parsed_calls) > 1 and all(
                f in READONLY_TOOL_NAMES for (_t, f, _a) in parsed_calls
            )
            if all_ro:
                import concurrent.futures as _cf

                results_map = {}
                with _cf.ThreadPoolExecutor(
                    max_workers=min(5, len(parsed_calls))
                ) as _ex:
                    _futs = {
                        _ex.submit(_exec_one, c): i for i, c in enumerate(parsed_calls)
                    }
                    for _f in _cf.as_completed(_futs):
                        results_map[_futs[_f]] = _f.result()
                results = [results_map[i] for i in range(len(parsed_calls))]
                _log(f"⚡ 并发执行 {len(parsed_calls)} 个只读工具")
            else:
                results = [_exec_one(c) for c in parsed_calls]

            # 3) 按原顺序回放结果（落库 + 回喂上下文）
            for (tc, fname, fargs), result in zip(parsed_calls, results):
                result_str = json.dumps(result, ensure_ascii=False)
                _ec = result.get("exit_code") if isinstance(result, dict) else None
                _err = result.get("error") if isinstance(result, dict) else None
                _log(
                    f"↩ {fname} -> "
                    + (
                        f"err: {_err}"
                        if _err
                        else (f"exit {_ec}" if _ec is not None else "ok")
                    )
                )
                add_turn(
                    task_id, "assistant", "tool_result", result_str, tool_name=fname
                )
                emit("tool_result", {"name": fname, "result": result})
                new_turns_log.append(
                    f"[工具 {fname}] 参数={json.dumps(fargs, ensure_ascii=False)[:200]} 结果={result_str[:400]}"
                )
                api_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id"),
                        "content": result_str,
                    }
                )

            if _check_cancel(task_id):
                interrupted = True
            if interrupted:
                break
        else:
            final_text = "（已达最大工具轮数，暂停。请查看进度卡后继续指示。）"

        if interrupted:
            final_text = final_text or "（已被用户中断，已保存当前进度，可继续指示。）"
            add_turn(task_id, "system", "text", "⏹️ 任务已被用户中断")

        if final_text:
            add_turn(task_id, "assistant", "text", final_text)
            emit("assistant", final_text)
            new_turns_log.append(f"[助手] {final_text}")

        # Q3 核心：强制刷新进度卡
        card = update_checkpoint(task, "\n".join(new_turns_log))
        emit("checkpoint", card)

        done = (not interrupted) and (
            "[TASK_DONE]" in final_text or card.get("status") == "已完成"
        )
        status = "已挂起" if interrupted else ("已完成" if done else "等待输入")
        update_task(
            task_id,
            status=status,
            progress=100 if done else int(card.get("progress", 10) or 10),
        )
        return {"final_text": final_text, "checkpoint": card, "done": done}

    except Exception as e:
        _log("回合执行出错:", e)
        add_turn(task_id, "system", "text", f"⚠️ 系统错误：{e}")
        update_task(task_id, status="失败")
        raise
    finally:
        _running[task_id] = False


def is_running(task_id):
    return bool(_running.get(task_id))


def request_cancel(task_id):
    """请求中断某任务：循环会在下一个检查点（每轮开头、每个工具之后）真正停下。"""
    with _cancel_lock:
        _cancel.add(task_id)
    _log(f"⏹ 收到中断请求 {task_id}")


def enqueue_message(task_id, text):
    """agent 工作时用户追加/修改需求：持久化为用户消息 + 入队，下一轮注入上下文。"""
    add_turn(task_id, "user", "text", text)
    with _queue_lock:
        _queue.setdefault(task_id, []).append(text)
    _log(f"📥 排队追加[{task_id}] {text[:40]}")
    return True


def _drain_queue(task_id):
    with _queue_lock:
        return _queue.pop(task_id, [])


def _check_cancel(task_id):
    with _cancel_lock:
        return task_id in _cancel


# ---------------------------------------------------------------------------
# 命令行入口（脱离 server 直接测试整个 agent）
# ---------------------------------------------------------------------------
def _cli():
    ap = argparse.ArgumentParser(description="Code Agent 命令行")
    sub = ap.add_subparsers(dest="cmd")
    p_new = sub.add_parser("new", help="新建任务并跑一轮")
    p_new.add_argument("goal")
    p_new.add_argument("--title", default=None)
    p_run = sub.add_parser("run", help="对已有任务发一条消息")
    p_run.add_argument("task_id")
    p_run.add_argument("msg")
    sub.add_parser("list", help="列出所有任务")
    p_show = sub.add_parser("show", help="查看任务进度卡与事件流")
    p_show.add_argument("task_id")
    args = ap.parse_args()
    init_db()

    def printer(kind, data):
        if kind == "tool_call":
            print(f"  [TOOL] {data['name']}({json.dumps(data['args'], ensure_ascii=False)[:80]})")
        elif kind == "subagents_start":
            print(f"  [FANOUT] 并发 {data['count']} 个子 agent (上限 {data['max_concurrency']})")
        elif kind == "subagent_done":
            print(f"  [DONE] 子任务[{data.get('id')}]: {str(data.get('result'))[:60]}")
        elif kind == "tool_result":
            print(f"  [RESULT] {json.dumps(data['result'], ensure_ascii=False)[:120]}")
        elif kind == "assistant":
            print(f"\n[AI] {data}\n")
        elif kind == "checkpoint":
            print(f"  [CARD] {data.get('summary')} ({data.get('progress')}%)")

    if args.cmd == "new":
        t = create_task(args.title or args.goal[:20], args.goal)
        print("任务:", t["id"], "工作区:", t["workspace"])
        run_agent_turn(t["id"], args.goal, on_event=printer)
    elif args.cmd == "run":
        run_agent_turn(args.task_id, args.msg, on_event=printer)
    elif args.cmd == "list":
        for t in list_tasks():
            print(f"{t['id']}  [{t['status']}] {t['progress']:>3}%  {t['title']}")
    elif args.cmd == "show":
        cp = get_checkpoint(args.task_id)
        print("=== 进度卡 ===")
        print(json.dumps(cp["card"] if cp else {}, ensure_ascii=False, indent=2))
        print("\n=== 事件流 ===")
        for t in get_turns(args.task_id):
            print(
                f"[{t['type']}] {t.get('tool_name') or t['role']}: {t['content'][:120]}"
            )
    else:
        ap.print_help()


if __name__ == "__main__":
    _cli()
