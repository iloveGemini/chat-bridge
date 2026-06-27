# -*- coding: utf-8 -*-
"""Code Agent 路由：任务 CRUD / 发送 / 中断 / 上下文 / 轮询 + 本地目录浏览。"""
import threading
from pathlib import Path

import runtime.coding_runtime as agent
from core.config import config, config_lock, save_config as _save_config
from agents.manager import run_coding
from routes.registry import post, get
from agents.coding.state import CodingState
try:
    from agents.coding.orchestrator import run_coding_task as _run_coding_orchestrator
except Exception:
    _run_coding_orchestrator = None


# ---------------- POST ----------------
@post("/api/agent/create")
def _create(h, query, session, session_id):
    data = h._read_json()
    task = agent.create_task(
        data.get("title") or "新任务",
        data.get("goal") or "",
        seed_dir=data.get("seed_dir") or None,
        work_dir=data.get("work_dir") or None,
    )
    h._json({"ok": True, "task": task})


@post("/api/agent/send")
def _send(h, query, session, session_id):
    data = h._read_json()
    task_id = data.get("task_id")
    text = (data.get("text") or "").strip()
    if not task_id or not text:
        h._json({"ok": False, "error": "task_id/text 必填"})
        return
    if agent.is_running(task_id):
        h._json({"ok": False, "error": "该任务正在运行中"})
        return
    # 网关路由：默认走新的 5 阶段 Orchestrator；config.coding.orchestrator=false 可回退旧大循环。
    with config_lock:
        _use_orch = (config.get("coding", {}) or {}).get("orchestrator", True)
    if _use_orch and _run_coding_orchestrator is not None:
        _runner, _mode = run_coding, "orchestrator"
    else:
        _runner, _mode = agent.run_agent_turn, "legacy"
    print(f"[agent/send] task={task_id} 路由 -> {_mode}")
    threading.Thread(target=_runner, args=(task_id, text), daemon=True).start()
    h._json({"ok": True, "mode": _mode})


@post("/api/agent/delete")
def _delete(h, query, session, session_id):
    data = h._read_json()
    agent.delete_task(data.get("task_id"))
    h._json({"ok": True})


@post("/api/agent/update")
def _update(h, query, session, session_id):
    data = h._read_json()
    tid = data.pop("task_id", None)
    if tid:
        agent.update_task(tid, **data)
    h._json({"ok": True, "task": agent.get_task(tid) if tid else None})


@post("/api/agent/interrupt")
def _interrupt(h, query, session, session_id):
    data = h._read_json()
    tid = data.get("task_id")
    if tid:
        agent.request_cancel(tid)
    h._json({"ok": True})


@post("/api/agent/confirm")
def _confirm(h, query, session, session_id):
    """用户确认「待确认」的工单 → 真正完结。校验只是静态/能起，
    是否满足需求由用户拍板，确认后才置完成。"""
    data = h._read_json()
    tid = data.get("task_id")
    if not tid:
        h._json({"ok": False, "error": "task_id 必填"})
        return
    if agent.is_running(tid):
        h._json({"ok": False, "error": "任务运行中，无法确认"})
        return
    agent.add_turn(tid, "system", "text", "✅ 用户已确认完成，工单关闭")
    agent.update_task(tid, status="已完成", progress=100)
    # 确认完成 → 提交最终状态(分块模式下这一步通常只兜底剩余改动) + 清空项目日志
    task = agent.get_task(tid)
    if task:
        try:
            title = task.get("title", "用户确认完成")
            r = agent.git_commit(task["workspace"], f"agent: {title}")
            if r.get("ok") and not r.get("empty") and not r.get("skipped"):
                agent.add_turn(tid, "system", "text", "📦 已提交最终改动")
        except Exception:
            pass
        try:
            from agents.coding.state import CodingState
            CodingState(task["workspace"]).set("journal", [])
        except Exception:
            pass
    h._json({"ok": True})


@get("/api/agent/mode")
def _get_mode(h, query, session, session_id):
    with config_lock:
        c = config.get("coding", {}) or {}
        v = bool(c.get("ask_before_acting", False))
        cpb = bool(c.get("commit_per_block", False))
    h._json({"ok": True, "ask_before_acting": v, "commit_per_block": cpb})


@post("/api/agent/mode")
def _set_mode(h, query, session, session_id):
    """切换全局开关：ask_before_acting(计划先批准) / commit_per_block(授权分块提交)。
    只更新本次传来的键。"""
    data = h._read_json()
    with config_lock:
        c = config.setdefault("coding", {})
        if "ask_before_acting" in data:
            c["ask_before_acting"] = bool(data.get("ask_before_acting"))
        if "commit_per_block" in data:
            c["commit_per_block"] = bool(data.get("commit_per_block"))
        out = {"ask_before_acting": bool(c.get("ask_before_acting", False)),
               "commit_per_block": bool(c.get("commit_per_block", False))}
    _save_config()
    h._json({"ok": True, **out})


@post("/api/agent/approve_plan")
def _approve_plan(h, query, session, session_id):
    """Ask Before Acting：用户批准计划 → 从 develop 续跑（不重新规划）。"""
    data = h._read_json()
    tid = data.get("task_id")
    if not tid:
        h._json({"ok": False, "error": "task_id 必填"})
        return
    if agent.is_running(tid):
        h._json({"ok": False, "error": "任务运行中"})
        return
    task = agent.get_task(tid)
    if not task:
        h._json({"ok": False, "error": "任务不存在"})
        return
    from agents.coding.state import CodingState
    CodingState(task["workspace"]).set("plan_approved", True)  # 批准 → 经理这轮派 developer 不再暂停
    agent.add_turn(tid, "system", "text", "▶️ 已批准计划，开始执行")
    threading.Thread(target=run_coding, args=(tid, ""), daemon=True).start()
    h._json({"ok": True})


@post("/api/agent/enqueue")
def _enqueue(h, query, session, session_id):
    data = h._read_json()
    tid = data.get("task_id")
    text = (data.get("text") or "").strip()
    if tid and text:
        agent.enqueue_message(tid, text)
        h._json({"ok": True})
    else:
        h._json({"ok": False, "error": "task_id/text 必填"})


@post("/api/agent/context/add")
def _ctx_add(h, query, session, session_id):
    data = h._read_json()
    h._json(agent.add_context(
        data.get("task_id"), data.get("filepath"), data.get("mode") or "outline"
    ))


@post("/api/agent/context/remove")
def _ctx_remove(h, query, session, session_id):
    data = h._read_json()
    h._json(agent.remove_context(data.get("task_id"), data.get("filepath")))


# ---------------- GET ----------------
@get("/api/agent/tasks")
def _tasks(h, query, session, session_id):
    h._json({"ok": True, "tasks": agent.list_tasks()})


@get("/api/agent/task")
def _task(h, query, session, session_id):
    tid = query.get("id", [""])[0]
    task = agent.get_task(tid)
    if not task:
        h._json({"ok": False, "error": "not found"}, 404)
        return
    
    # 附加 todos
    try:
        task["todos"] = CodingState(task["workspace"]).get_todos()
    except Exception:
        task["todos"] = []

    cp = agent.get_checkpoint(tid)
    h._json({
        "ok": True,
        "task": task,
        "checkpoint": cp["card"] if cp else None,
        "running": agent.is_running(tid),
        "tree": agent.workspace_tree(tid),
    })


@get("/api/agent/turns")
def _turns(h, query, session, session_id):
    tid = query.get("id", [""])[0]
    after = int(query.get("after", ["0"])[0] or 0)
    h._json({"ok": True, "turns": agent.get_turns(tid, after), "running": agent.is_running(tid)})


@get("/api/logs")
def _logs(h, query, session, session_id):
    after = int(query.get("after", ["0"])[0] or 0)
    logs = agent.get_debug(after)
    h._json({"ok": True, "logs": logs, "seq": (logs[-1]["id"] if logs else after)})


@get("/api/agent/last_prompt")
def _last_prompt(h, query, session, session_id):
    tid = query.get("id", [""])[0]
    h._json({"ok": True, "last_prompt": agent.get_last_prompt(tid)})


@get("/api/agent/context")
def _ctx(h, query, session, session_id):
    tid = query.get("id", [""])[0]
    h._json({"ok": True, "context": agent.list_context(tid)})


@get("/api/agent/files")
def _files(h, query, session, session_id):
    tid = query.get("id", [""])[0]
    h._json({"ok": True, "files": agent.list_workspace_files(tid)})


@get("/api/fs/list")
def _fs_list(h, query, session, session_id):
    """服务端目录浏览（给前端文件夹选择器用）。只返回目录，不返回文件内容。"""
    raw = query.get("path", [""])[0]
    try:
        import os
        if not raw:
            if os.name == "nt":
                import string
                roots = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
                h._json({"ok": True, "path": "", "parent": None, "dirs": roots})
            else:
                base = Path("/")
                dirs = []
                for x in sorted(base.iterdir(), key=lambda z: z.name.lower()):
                    try:
                        if x.is_dir() and not x.name.startswith("."):
                            dirs.append(str(x))
                    except (PermissionError, OSError):
                        pass
                h._json({"ok": True, "path": "/", "parent": None, "dirs": dirs})
            return
        pp = Path(raw)
        if not pp.exists() or not pp.is_dir():
            h._json({"ok": False, "error": "目录不存在"})
            return
        dirs = []
        for x in sorted(pp.iterdir(), key=lambda z: z.name.lower()):
            try:
                if x.is_dir() and not x.name.startswith("."):
                    dirs.append(str(x))
            except (PermissionError, OSError):
                pass
        parent = str(pp.parent) if str(pp.parent) != str(pp) else ""
        h._json({"ok": True, "path": str(pp), "parent": parent, "dirs": dirs})
    except Exception as ex:
        h._json({"ok": False, "error": str(ex)})
