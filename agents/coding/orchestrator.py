# -*- coding: utf-8 -*-
"""
Coding Agent 的核心调度器 (Orchestrator) —— 5 阶段状态机。

流程：Plan -> Search -> Code -> Write -> Check
- Plan(planner)   : 不写代码，只拆解任务/提问/规划。需求不清时推确认卡并暂停等用户。
- Search(searcher): 只读侦察兵，按规划找回精准上下文。
- Code(coder)     : 高级开发，只产出结构化修改意图(Diffs)，不碰文件。
- Write(writer)   : 打字员，把 Diffs 精确落地为 apply_file_edits / replace_in_file 调用。
- Check(checker)  : QA，跑测试/校验。Pass -> 任务完成；Fail -> 把报错回喂给 Planner 进入下一轮。

设计要点：
- 工具是全局共享资源池(tools/registry)，按角色 RBAC 分配(get_tools)。
- 提示词从 agents/coding/prompts/*.md 解耦加载(ROLE_PROMPTS)。
- 复用 agent.py 里已经过实战的基础设施：LLM 调用、沙箱上下文、落库、取消/队列。
  Coding Agent 在此被「降级」为生态中的一个子系统，agent.py 退化为底层能力库。
"""
import json

import runtime.coding_runtime as agent
from agents.coding.state import CodingState
from agents.coding.roles import ROLE_PROMPTS
from tools.registry import get_tools, execute_tool
from agents.engine import run_tool_loop
from agents.coding import phase as _phase  # noqa: F401  (导入即注册 5 个相位叶子)
from agents.base import AgentContext, AgentResult, get_agent
from agents.manager import BaseManager

PHASE_TO_ROLE = {
    "plan": "planner",
    "search": "searcher",
    "code": "coder",
    "write": "writer",
    "check": "checker",
}
PHASE_SEQUENCE = ["plan", "search", "code", "write", "check"]

# 每个相位用哪个逻辑端点：searcher/writer 是体力活(只读侦察/把 Diffs 落地)，
# 交给便宜高 RPM 的 worker_api；planner/coder/checker 是脑力活，留给主模型 api。
# worker_api 未配置时 _resolve_api 会自动回落到 summary_api / api，安全。
PHASE_ENDPOINT = {
    "plan": "api",
    "search": "worker_api",
    "code": "api",
    "write": "worker_api",
    "check": "api",
}

PHASE_MAX_ROUNDS = {
    "plan": 6,
    "search": 14,
    "code": 4,
    "write": 14,
    "check": 8,
}
MAX_CYCLES = 3


class CodingOrchestrator(BaseManager):
    agent_type = "coding"

    def __init__(self, task_id, workspace_dir, chat_fn=None):
        self.task_id = task_id
        self.workspace_dir = workspace_dir
        self.state = CodingState(workspace_dir)
        self.task = None
        self.ctx = None
        self._on_event = None
        self._custom_chat = chat_fn  # 测试可注入；为 None 时按相位选逻辑端点

    def _chat_for(self, endpoint):
        """返回一个绑定到指定逻辑端点(api/worker_api/...)的 chat_fn。
        searcher/writer 走便宜高 RPM 的 worker_api，省钱也省主模型额度。"""
        custom = self._custom_chat

        def _fn(messages, tools):
            # 记录最近一次 payload，供前端「Last Prompt」调试按钮查看（两条路径统一）。
            try:
                agent.set_last_prompt(
                    self.task_id, messages, phase=self.state.get("phase")
                )
            except Exception:
                pass
            if custom:
                return custom(messages, tools)
            return agent._chat(
                endpoint, messages, tools=tools or None, temperature=0.3
            )

        return _fn

    def _emit(self, kind, data):
        if self._on_event:
            try:
                self._on_event(kind, data)
            except Exception:
                pass

    def _workspace_tree(self):
        try:
            return agent.workspace_tree(self.task_id)
        except Exception:
            return ""

    def _build_handoff(self, phase, user_msg, shared):
        d = shared
        goal = (self.task or {}).get("goal") or ""
        base = f"【原始需求】\n{user_msg or goal or '(沿用既有任务目标)'}"
        if phase == "plan":
            out = base
            if d.get("last_error"):
                out += (
                    "\n\n【上一轮验证失败的报错（请据此修正计划）】\n"
                    + d["last_error"]
                )
            return out
        if phase == "search":
            return base + "\n\n【规划者给出的方案/搜索意图】\n" + (d.get("plan_text") or "")
        if phase == "code":
            return (
                base
                + "\n\n【规划】\n" + (d.get("plan_text") or "")
                + "\n\n【侦察兵找回的上下文】\n" + (d.get("search_text") or "")
                + "\n\n请只输出结构化的修改意图(Diffs)：每条写清 文件路径 / 起止行 / 改成什么，"
                "不要调用任何工具。"
            )
        if phase == "write":
            return (
                "请把以下「修改意图(Diffs)」精确翻译成 apply_file_edits / replace_in_file / "
                "batch_write_files 工具调用并落地，不要改变其逻辑：\n\n" + (d.get("diffs_text") or "")
            )
        if phase == "check":
            return (
                base
                + "\n\n代码改动已应用。请运行验证（优先 pytest / 项目测试，其次 node -c、最小运行）。"
                "全部通过请在结尾单独输出标记 [CHECK_PASS]；"
                "若有失败请在结尾输出 [CHECK_FAIL] 并附上关键报错日志。"
            )
        return base

    # ---------- FlowManager 钩子：相位流转（在 ctx.shared 上自治） ----------
    def entry(self, ctx):
        cyc = int(self.state.get("cycle", 0) or 0)
        ctx.shared["cycle"] = cyc
        if cyc >= MAX_CYCLES:
            ctx.shared["_exhausted"] = True
            return None
        self._emit("cycle", {"cycle": cyc})
        return PHASE_SEQUENCE[0]

    def advance(self, ctx, last_step, last_result=None):
        if last_step != "check":
            return PHASE_SEQUENCE[PHASE_SEQUENCE.index(last_step) + 1]
        if ctx.shared.get("_check_passed"):
            return None  # check 通过：done 已在 _capture 置位
        cyc = ctx.shared["cycle"] + 1
        ctx.shared["cycle"] = cyc
        self.state.set("cycle", cyc)
        if cyc >= MAX_CYCLES:
            ctx.shared["_exhausted"] = True
            return None
        self._emit("cycle", {"cycle": cyc})
        return PHASE_SEQUENCE[0]

    def _capture(self, ctx, phase, text):
        if phase == "plan":
            ctx.shared["plan_text"] = text; self.state.set("plan_text", text)
        elif phase == "search":
            ctx.shared["search_text"] = text; self.state.set("search_text", text)
        elif phase == "code":
            ctx.shared["diffs_text"] = text; self.state.set("diffs_text", text)
        elif phase == "check":
            passed = ("[CHECK_PASS]" in text) and ("[CHECK_FAIL]" not in text)
            ctx.shared["_check_passed"] = passed
            if passed:
                ctx.shared["done"] = True
                ctx.shared["final_text"] = text
            else:
                ctx.shared["last_error"] = text or "(验证未通过且无明确报错)"
                self.state.set("last_error", ctx.shared["last_error"])

    def _run_phase(self, phase, handoff):
        # 路由到该相位的注册叶子节点；运行时依赖与 handoff 经 ctx.shared 交接，
        # 叶子把正文写回 ctx.shared['{role}_text']。返回结构与原实现逐字节一致。
        role = PHASE_TO_ROLE[phase]
        ctx = AgentContext(
            task_id=self.task_id,
            on_event=self._on_event,
            shared={
                "chat_fn": self._chat_for(PHASE_ENDPOINT.get(phase, "api")),
                "tool_ctx": self.ctx,
                "workspace_tree": self._workspace_tree(),
                "is_cancelled": lambda: agent._check_cancel(self.task_id),
                "handoff": handoff,
                "max_rounds": PHASE_MAX_ROUNDS.get(phase, 6),
            },
        )
        leaf = get_agent(f"coding.{role}")
        ar = leaf.run(ctx)
        text = ctx.shared.get(f"{role}_text", "")
        if ar.next_hint == "cancelled":
            return {"cancelled": True, "text": ""}
        if ar.next_hint == "clarify":
            return {"clarify": True, "clarify_payload": ctx.shared.get("clarify_payload"), "text": text}
        return {"text": text}

    def run(self, ctx: AgentContext) -> AgentResult:
        """统一节点接口：把 AgentContext 适配到 run_turn —— coding 作为组合 Manager 节点被上层路由。"""
        res = self.run_turn(
            ctx.user_msg,
            {"task": (ctx.shared or {}).get("task"), "on_event": ctx.on_event},
        )
        status = "done" if res.get("done") else "need_user"
        return AgentResult(status=status, output=res.get("final_text"))

    def run_turn(self, user_msg, context):
        context = context or {}
        self.task = context.get("task") or agent.get_task(self.task_id)
        if not self.task:
            raise ValueError(f"任务不存在: {self.task_id}")
        self._on_event = context.get("on_event")
        self.ctx = agent._sandbox_context(self.task)

        if agent._running.get(self.task_id):
            raise RuntimeError("该任务正在运行中")
        agent._running[self.task_id] = True
        with agent._cancel_lock:
            agent._cancel.discard(self.task_id)

        try:
            agent.update_task(self.task_id, status="运行中")
            self.state.update(status="running", last_error="")
            if user_msg:
                agent.add_turn(self.task_id, "user", "text", user_msg)
                self._emit("user", user_msg)

            self.state.set_phase("plan")

            ctx = AgentContext(
                task_id=self.task_id, user_msg=user_msg,
                on_event=self._on_event, shared={},
            )
            # 从持久化状态播种 ctx.shared（断点续跑时承接上轮上下文）
            for _k in ("plan_text", "search_text", "diffs_text", "last_error"):
                ctx.shared[_k] = self.state.get(_k, "") or ""

            final_text = ""
            done = False
            waiting_user = False
            interrupted = False

            step = self.entry(ctx)
            while step is not None:
                if agent._check_cancel(self.task_id):
                    interrupted = True
                    break
                injected = agent._drain_queue(self.task_id)

                self.state.set_phase(step)
                self._emit("phase", {"phase": step, "role": PHASE_TO_ROLE[step]})

                handoff = self._build_handoff(step, user_msg, ctx.shared)
                if injected:
                    handoff += "\n\n【用户追加/修改需求，请纳入考虑】\n" + "\n".join(injected)

                res = self._run_phase(step, handoff)

                if res.get("cancelled"):
                    interrupted = True
                    break
                if res.get("clarify"):
                    waiting_user = True
                    final_text = "（已推送需求确认卡，等待你的选择/补充。）"
                    break

                self._capture(ctx, step, res.get("text", "") or "")

                # 规划者还在收集需求/向用户提问（纯文字、没推确认卡）时，
                # 它会在结尾打 [NEED_USER] 标记 -> 暂停等用户，别空跑后面 4 个相位。
                if step == "plan":
                    _pt = ctx.shared.get("plan_text", "") or ""
                    if "[NEED_USER]" in _pt:
                        waiting_user = True
                        final_text = (
                            _pt.replace("[NEED_USER]", "").strip()
                            or "（规划者需要你补充需求后再继续。）"
                        )
                        break

                step = self.advance(ctx, step)

            if not (interrupted or waiting_user):
                if ctx.shared.get("done"):
                    done = True
                    final_text = ctx.shared.get("final_text", "")
                elif ctx.shared.get("_exhausted"):
                    final_text = "（已达最大循环次数，验证仍未通过，暂停等待你的指示。）"
                else:
                    final_text = final_text or "（流水线已跑完一轮，请查看进度并指示下一步。）"

            if interrupted:
                final_text = final_text or "（已被用户中断，已保存当前进度，可继续指示。）"
                agent.add_turn(self.task_id, "system", "text", "⏹️ 任务已被用户中断")

            if final_text:
                agent.add_turn(self.task_id, "assistant", "text", final_text)
                self._emit("assistant", final_text)

            card = {}
            try:
                card = agent.update_checkpoint(self.task, final_text or "")
                self._emit("checkpoint", card)
            except Exception:
                pass

            status = (
                "已挂起" if interrupted
                else ("等待输入" if waiting_user else ("已完成" if done else "等待输入"))
            )
            self.state.set(
                "status",
                "failed" if interrupted else ("waiting_user" if waiting_user else ("done" if done else "idle")),
            )
            if done:
                self.state.set_phase("done")
            agent.update_task(
                self.task_id,
                status=status,
                progress=100 if done else int((card or {}).get("progress", 10) or 10),
            )
            return {
                "final_text": final_text,
                "phase": self.state.get("phase"),
                "done": done,
                "waiting_user": waiting_user,
                "interrupted": interrupted,
                "checkpoint": card,
            }

        except Exception as e:
            agent._log("Orchestrator 回合出错:", e)
            agent.add_turn(self.task_id, "system", "text", f"⚠️ 系统错误：{e}")
            agent.update_task(self.task_id, status="失败")
            self.state.set("status", "failed")
            raise
        finally:
            agent._running[self.task_id] = False


def run_coding_task(task_id, user_msg, on_event=None):
    """模块级入口，签名对齐 agent.run_agent_turn，供 server.py 网关调用。"""
    task = agent.get_task(task_id)
    if not task:
        raise ValueError(f"任务不存在: {task_id}")
    orch = CodingOrchestrator(task_id, task["workspace"])
    return orch.run_turn(user_msg, {"task": task, "on_event": on_event})
