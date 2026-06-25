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

import agent
from agents.coding.state import CodingState
from agents.coding.roles import ROLE_PROMPTS
from tools.registry import get_tools, execute_tool

PHASE_TO_ROLE = {
    "plan": "planner",
    "search": "searcher",
    "code": "coder",
    "write": "writer",
    "check": "checker",
}
PHASE_SEQUENCE = ["plan", "search", "code", "write", "check"]

PHASE_MAX_ROUNDS = {
    "plan": 6,
    "search": 14,
    "code": 4,
    "write": 14,
    "check": 8,
}
MAX_CYCLES = 3


class CodingOrchestrator:
    def __init__(self, task_id, workspace_dir, chat_fn=None):
        self.task_id = task_id
        self.workspace_dir = workspace_dir
        self.state = CodingState(workspace_dir)
        self.task = None
        self.ctx = None
        self._on_event = None
        self._chat_fn = chat_fn or (lambda messages, tools: agent._chat(
            "api", messages, tools=tools or None, temperature=0.3
        ))

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

    def _phase_messages(self, role, handoff):
        sys_prompt = ROLE_PROMPTS.get(role, ROLE_PROMPTS["planner"])
        tree = self._workspace_tree()
        sys_full = (
            sys_prompt
            + "\n\n【环境】沙箱为 Windows，所有路径相对工作区根目录。"
            + (f"\n\n【工作区文件树】\n{tree}" if tree else "")
        )
        return [
            {"role": "system", "content": sys_full},
            {"role": "user", "content": handoff},
        ]

    def _build_handoff(self, phase, user_msg):
        d = self.state.data
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

    def _run_phase(self, phase, handoff):
        role = PHASE_TO_ROLE[phase]
        tools = get_tools(role)
        messages = self._phase_messages(role, handoff)
        max_rounds = PHASE_MAX_ROUNDS.get(phase, 6)
        final_text = ""

        for _round in range(max_rounds):
            if agent._check_cancel(self.task_id):
                return {"cancelled": True, "text": final_text}

            res = self._chat_fn(messages, tools)
            choice = res["choices"][0]["message"]
            tcs = choice.get("tool_calls") or []
            content = (choice.get("content") or "").strip()

            if content:
                agent.add_turn(self.task_id, "assistant", "text", f"[{role}] {content}")
                self._emit("assistant", f"[{role}] {content}")

            if not tcs:
                final_text = content
                break

            messages.append(
                {"role": "assistant", "content": choice.get("content") or "", "tool_calls": tcs}
            )
            for tc in tcs:
                fn = tc.get("function", {}) or {}
                fname = fn.get("name", "")
                try:
                    fargs = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    fargs = {}
                agent.add_turn(
                    self.task_id, "assistant", "tool_call",
                    {"name": fname, "args": fargs}, tool_name=fname,
                )
                self._emit("tool_call", {"name": fname, "args": fargs})

                if fname == "ask_user_clarification":
                    self._emit("clarification", fargs)
                    agent.add_turn(
                        self.task_id, "assistant", "tool_result",
                        json.dumps({"clarify": True}, ensure_ascii=False), tool_name=fname,
                    )
                    return {"clarify": True, "clarify_payload": fargs, "text": content}

                result = execute_tool(fname, fargs, self.ctx)
                result_str = json.dumps(result, ensure_ascii=False)
                agent.add_turn(
                    self.task_id, "assistant", "tool_result", result_str, tool_name=fname
                )
                self._emit("tool_result", {"name": fname, "result": result})
                messages.append(
                    {"role": "tool", "tool_call_id": tc.get("id"), "content": result_str}
                )
        return {"text": final_text}

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

            final_text = ""
            done = False
            waiting_user = False
            interrupted = False

            cycle = int(self.state.get("cycle", 0) or 0)
            while cycle < MAX_CYCLES:
                self._emit("cycle", {"cycle": cycle})
                restart_cycle = False

                for phase in PHASE_SEQUENCE:
                    if agent._check_cancel(self.task_id):
                        interrupted = True
                        break
                    injected = agent._drain_queue(self.task_id)

                    self.state.set_phase(phase)
                    self._emit("phase", {"phase": phase, "role": PHASE_TO_ROLE[phase]})

                    handoff = self._build_handoff(phase, user_msg)
                    if injected:
                        handoff += "\n\n【用户追加/修改需求，请纳入考虑】\n" + "\n".join(injected)

                    res = self._run_phase(phase, handoff)

                    if res.get("cancelled"):
                        interrupted = True
                        break
                    if res.get("clarify"):
                        waiting_user = True
                        final_text = "（已推送需求确认卡，等待你的选择/补充。）"
                        break

                    text = res.get("text", "") or ""
                    if phase == "plan":
                        self.state.set("plan_text", text)
                    elif phase == "search":
                        self.state.set("search_text", text)
                    elif phase == "code":
                        self.state.set("diffs_text", text)
                    elif phase == "check":
                        passed = ("[CHECK_PASS]" in text) and ("[CHECK_FAIL]" not in text)
                        if passed:
                            done = True
                            final_text = text
                        else:
                            self.state.set("last_error", text or "(验证未通过且无明确报错)")
                            restart_cycle = True

                if interrupted or waiting_user or done:
                    break
                if restart_cycle:
                    cycle += 1
                    self.state.set("cycle", cycle)
                    continue
                final_text = final_text or "（流水线已跑完一轮，请查看进度并指示下一步。）"
                break
            else:
                final_text = "（已达最大循环次数，验证仍未通过，暂停等待你的指示。）"

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
