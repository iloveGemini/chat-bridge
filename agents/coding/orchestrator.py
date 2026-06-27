# -*- coding: utf-8 -*-
"""
Coding Agent 的核心调度器 —— planner 即「项目经理」的动态调度循环。

不再是写死的线性流水线，而是一个主管(supervisor)模式：
  planner 是项目经理，自己不读不写不跑，只统筹。它每一轮看当前进展，决定
  「现在派谁去干什么」——通过 dispatch 工具调度子 agent：
    - searcher  : 只读侦察/查代码（worker_api，便宜快）
    - developer : 改代码（读+写，主模型）
    - checker   : 跑测试/验证（主模型）
  直到它判断完成 -> finish(进「待确认」闸门)，或需要用户拍板 -> ask_user_clarification。

代码退化成很薄的执行器：调 planner -> 跑它派的活 -> 结果回喂 -> 再调 planner。
当前为「串行」执行（一次跑一个），并发以后再加。
"""

import json

import runtime.coding_runtime as agent
from agents.base import AgentContext, AgentResult, get_agent
from agents.coding import phase as _phase  # noqa: F401  (导入即注册各相位叶子)
from agents.coding.state import CodingState
from agents.manager import BaseManager
from tools.registry import _REGISTRY

# 可被项目经理调度的子 agent，及其逻辑端点 / 工具轮数上限
DISPATCHABLE = ("searcher", "developer", "checker")
WORKER_ENDPOINT = {
    "searcher": "worker_api",
    "developer": "api",
    "checker": "worker_api",
}
WORKER_MAX_ROUNDS = {"searcher": 20, "developer": 20, "checker": 20}
MAX_MANAGER_ROUNDS = 16  # 项目经理最多调度多少轮，封顶防失控


def _dispatch_tool():
    return {
        "type": "function",
        "function": {
            "name": "dispatch",
            "description": (
                "派一个子 agent 去干一件具体的事并拿回结果。你是项目经理，自己不读文件、"
                "不写代码、不跑命令，只能通过它调度。一次只派一个。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "agent": {
                        "type": "string",
                        "enum": list(DISPATCHABLE),
                        "description": "searcher=只读侦察/查代码; developer=改代码(读+写); checker=跑测试/验证",
                    },
                    "instruction": {
                        "type": "string",
                        "description": "交给该 agent 的明确任务(查什么/改什么/验证什么)，尽量自包含。",
                    },
                    "reason": {
                        "type": "string",
                        "description": "一句话说明你此刻的判断、为什么派这个 agent 干这件事（会作为进度旁白展示给用户）。",
                    },
                },
                "required": ["agent", "instruction", "reason"],
            },
        },
    }


def _finish_tool():
    return {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "任务已完成（通常在 checker 验证通过后）。提交改动小结，转交用户确认。",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "做了哪些改动的小结"}
                },
                "required": ["summary"],
            },
        },
    }


def _commit_tool():
    return {
        "type": "function",
        "function": {
            "name": "commit",
            "description": (
                "把当前已完成的一个功能块用 git 提交(add -A && commit)。仅在完成一个相对独立、"
                "已验证的功能单元后调用，便于出问题时按块回退，不必整个推倒。message 一句话描述这块改了什么。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "这次提交说明(一句话，描述该功能块的改动)",
                    }
                },
                "required": ["message"],
            },
        },
    }


def _manager_tools(commit_enabled=False):
    tools = [_dispatch_tool(), _finish_tool()]
    if commit_enabled:
        tools.append(_commit_tool())  # 仅在「分块提交」授权开启时给经理 commit 能力
    clar = _REGISTRY.get("ask_user_clarification")
    if clar:
        tools.append(clar["schema"])

    # 允许 Manager 主动管理文件上下文
    add_ws = _REGISTRY.get("add_workspace_file")
    if add_ws:
        tools.append(add_ws["schema"])
    rm_ws = _REGISTRY.get("remove_workspace_file")
    if rm_ws:
        tools.append(rm_ws["schema"])

    return tools


class CodingOrchestrator(BaseManager):
    agent_type = "coding"

    def __init__(self, task_id, workspace_dir, chat_fn=None):
        self.task_id = task_id
        self.workspace_dir = workspace_dir
        self.state = CodingState(workspace_dir)
        self.task = None
        self.ctx = None
        self._on_event = None
        self._custom_chat = chat_fn  # 测试可注入；为 None 时按角色选逻辑端点

    def _chat_for(self, endpoint):
        """返回绑定到某逻辑端点(api/worker_api)的 chat_fn，并记录 last_prompt。"""
        custom = self._custom_chat

        def _fn(messages, tools):
            try:
                agent.set_last_prompt(
                    self.task_id, messages, phase=self.state.get("phase")
                )
            except Exception:
                pass
            if custom:
                return custom(messages, tools)
            return agent._chat(endpoint, messages, tools=tools or None, temperature=0.3)

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

    def _ask_before_acting(self):
        """全局模式开关：开启时，派 developer 动文件前先暂停让用户批准。"""
        try:
            return bool(
                (agent.load_config().get("coding", {}) or {}).get(
                    "ask_before_acting", False
                )
            )
        except Exception:
            return False

    def _commit_per_block(self):
        """全局开关：授权经理在大改动里按功能块分次 git 提交。"""
        try:
            return bool(
                (agent.load_config().get("coding", {}) or {}).get(
                    "commit_per_block", False
                )
            )
        except Exception:
            return False

    def _recap(self, user_msg):
        """续跑/返工的前情提要：最近若干条对话，让经理承接已知进展、不从零重问。"""
        try:
            turns = agent.get_turns(self.task_id) or []
        except Exception:
            turns = []
        texts = [
            t
            for t in turns
            if t.get("type") == "text" and t.get("role") in ("user", "assistant")
        ]
        if (
            texts
            and texts[-1].get("role") == "user"
            and (texts[-1].get("content") or "").strip() == (user_msg or "").strip()
        ):
            texts = texts[:-1]
        if not texts:
            return ""
        lines = []
        for t in texts[-8:]:
            who = "用户" if t.get("role") == "user" else "助手"
            c = (t.get("content") or "").strip().replace("\n", " ")
            if len(c) > 280:
                c = c[:280] + "…"
            lines.append(f"[{who}] {c}")
        return "（最近对话）\n" + "\n".join(lines)

    # ---------------- 子 agent 执行（串行） ----------------
    def _worker_handoff(self, agent_name, instruction, progress_text):
        req = (
            self.state.get("user_request", "")
            or (self.task or {}).get("goal", "")
            or ""
        ).strip()
        head = f"【总目标】\n{req}\n\n【你这次的任务】\n{instruction}"
        if agent_name in ("developer", "checker") and progress_text:
            head += (
                "\n\n【已掌握的资料 / 已做的改动（直接用，不必重复侦察）】\n"
                + progress_text[:9000]
            )
        if agent_name == "developer":
            head += (
                "\n\n务必**真正调用工具**(apply_file_edits/replace_in_file/batch_write_files)"
                "把改动落地，不要只输出 Diffs。改完一两句话简述改了哪些文件、做了什么。"
            )
        if agent_name == "checker":
            head += (
                "\n\n请运行验证（优先 pytest / 项目测试，其次 node -c / 最小运行）。"
                "如果需要测试网页交互，可以使用 run_playwright_script 工具编写并执行自动化测试脚本。"
                "全部通过在结尾单独输出 [CHECK_PASS]；失败输出 [CHECK_FAIL] 并附关键报错。"
            )
        return head

    def _run_worker(self, agent_name, instruction, progress_text):
        """跑一个被派的子 agent，返回 {'text':..,'cancelled':bool}。复用相位叶子。"""
        leaf = get_agent(f"coding.{agent_name}")
        if not leaf:
            return {"text": f"(未知 agent: {agent_name})", "cancelled": False}
        ctx = AgentContext(
            task_id=self.task_id,
            on_event=self._on_event,
            shared={
                "chat_fn": self._chat_for(WORKER_ENDPOINT.get(agent_name, "api")),
                "tool_ctx": self.ctx,
                "workspace_tree": self._workspace_tree(),
                "is_cancelled": lambda: agent._check_cancel(self.task_id),
                "handoff": self._worker_handoff(agent_name, instruction, progress_text),
                "max_rounds": WORKER_MAX_ROUNDS.get(agent_name, 8),
            },
        )
        ar = leaf.run(ctx)
        text = ctx.shared.get(f"{agent_name}_text", "") or ""
        if ctx.shared.get(f"{agent_name}_stop") == "max_rounds":
            text += "\n\n警告：该 agent 已耗尽最大轮数，可能未完成任务或未做任何修改"
        return {
            "text": text,
            "cancelled": getattr(ar, "next_hint", None) == "cancelled",
        }

    # ---------------- 统一节点接口 ----------------
    def run(self, ctx: AgentContext) -> AgentResult:
        res = self.run_turn(
            ctx.user_msg,
            {"task": (ctx.shared or {}).get("task"), "on_event": ctx.on_event},
        )
        status = "done" if res.get("done") else "need_user"
        return AgentResult(status=status, output=res.get("final_text"))

    # ---------------- 主管调度循环 ----------------
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
            self.state.update(status="running")
            self.state.set_phase("manage")
            if user_msg:
                agent.add_turn(self.task_id, "user", "text", user_msg)
                if not self.state.get("user_request"):
                    self.state.set("user_request", user_msg)
                else:
                    self.state.set("latest_user_reply", user_msg)
                self.state.set("plan_approved", False)  # 新需求/返工 → 重新需要批准
                self._emit("user", user_msg)

            final_text = ""
            done = False
            waiting_user = False
            interrupted = False
            await_confirm = False
            await_plan_approval = False

            # 经理上下文：原始需求 + 前情提要
            req = (
                self.state.get("user_request", "")
                or (self.task or {}).get("goal", "")
                or "(沿用既有任务目标)"
            )
            latest_reply = self.state.get("latest_user_reply", "")
            if latest_reply and latest_reply != req:
                req += f"\n\n【用户最新回复】\n{latest_reply}"
            sys_prompt = _phase.load_role_prompt("planner")
            tree = self._workspace_tree()
            sys_full = (
                sys_prompt
                + "\n\n【环境】沙箱为 Windows，所有路径相对工作区根目录。"
                + (f"\n\n【工作区文件树】\n{tree}" if tree else "")
            )
            # 持久化项目日志：经理的决策 + 各 worker 的结果都记进 state，
            # 跨轮(暂停/批准/返工)回放，让经理在任务 finish 前始终有全局记忆。
            journal = self.state.get("journal", []) or []
            if not isinstance(journal, list):
                journal = []

            def _jadd(entry):
                journal.append(entry)
                try:
                    self.state.set("journal", journal[-60:])  # 截断防膨胀
                except Exception:
                    pass

            def _jtext(cap):
                s = "\n".join(journal)
                return s[-cap:] if len(s) > cap else s

            user0 = f"【原始需求】\n{req}"
            if journal:
                user0 += (
                    "\n\n【项目进度记录（本任务已做过的事，承接它继续，别重做/别重新问已知信息）】\n"
                    + _jtext(24000)
                )
            recap = self._recap(user_msg)
            if recap:
                user0 += "\n\n【前情提要】\n" + recap
            messages = [
                {"role": "system", "content": sys_full},
                {"role": "user", "content": user0},
            ]

            commit_enabled = self._commit_per_block()
            tools = _manager_tools(commit_enabled)
            manager_chat = self._chat_for("api")
            approved = bool(self.state.get("plan_approved"))

            for _round in range(MAX_MANAGER_ROUNDS):
                if agent._check_cancel(self.task_id):
                    interrupted = True
                    break
                injected = agent._drain_queue(self.task_id)
                if injected:
                    messages.append(
                        {
                            "role": "user",
                            "content": "【用户追加/修改需求，请纳入考虑】\n"
                            + "\n".join(injected),
                        }
                    )

                res = manager_chat(messages, tools)
                choice = (res.get("choices") or [{}])[0].get("message", {}) or {}
                content = (choice.get("content") or "").strip()
                tcs = choice.get("tool_calls") or []

                if content:
                    agent.add_turn(
                        self.task_id, "assistant", "text", f"[planner] {content}"
                    )
                    self._emit("assistant", f"[planner] {content}")
                    _jadd(f"经理：{content[:600]}")

                if not tcs:
                    # 经理只说话没派活 → 多半在征求意见 / 需要你拍板
                    waiting_user = True
                    final_text = content or "（需要你的进一步指示。）"
                    break

                messages.append(
                    {
                        "role": "assistant",
                        "content": choice.get("content") or "",
                        "tool_calls": tcs,
                    }
                )

                stop = False
                for tc in tcs:
                    fn = tc.get("function", {}) or {}
                    name = fn.get("name", "")
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except Exception:
                        args = {}

                    if name == "ask_user_clarification":
                        agent.add_turn(
                            self.task_id,
                            "assistant",
                            "clarification_card",
                            json.dumps(args, ensure_ascii=False),
                            tool_name=name,
                        )
                        self._emit("clarification", args)
                        waiting_user = True
                        final_text = "（已推送需求确认卡，等待你的选择/补充。）"
                        stop = True
                        break

                    if name == "finish":
                        await_confirm = True
                        final_text = (
                            args.get("summary") or ""
                        ).strip() or "改动已完成，待你确认。"
                        stop = True
                        break

                    if name == "commit":
                        msg = (args.get("message") or "").strip() or "WIP"
                        r = agent.git_commit(self.task["workspace"], f"agent: {msg}")
                        tip = r.get("msg", "")
                        agent.add_turn(
                            self.task_id,
                            "system",
                            "text",
                            f"📦 提交：{msg}" + (f"（{tip}）" if tip else ""),
                        )
                        _jadd(f"已 git 提交：{msg}")
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.get("id"),
                                "content": json.dumps(r, ensure_ascii=False),
                            }
                        )
                        continue

                    if name == "dispatch":
                        agent_name = args.get("agent", "")
                        instruction = (args.get("instruction") or "").strip()
                        # 经理的旁白：把它派单的理由讲出来（模型调工具时 content 常为空，靠这个补上）
                        reason = (args.get("reason") or "").strip()
                        if reason:
                            agent.add_turn(
                                self.task_id, "assistant", "text", f"[planner] {reason}"
                            )
                            self._emit("assistant", f"[planner] {reason}")
                            _jadd(f"经理：{reason[:400]}")
                        if agent_name not in DISPATCHABLE:
                            messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": tc.get("id"),
                                    "content": f"未知 agent：{agent_name}，可选 {list(DISPATCHABLE)}",
                                }
                            )
                            continue
                        # Ask Before Acting：派 developer 动文件前先等批准
                        # 如果用户发了文字回复(user_msg)，放行让 planner 看到并重新规划，不再死板拦截
                        if (
                            agent_name == "developer"
                            and self._ask_before_acting()
                            and not approved
                            and not user_msg
                        ):
                            await_plan_approval = True
                            final_text = (
                                (content + "\n\n") if content else ""
                            ) + f"即将派 developer 执行：\n{instruction}"
                            stop = True
                            break

                        agent.add_turn(
                            self.task_id,
                            "system",
                            "text",
                            f"🧭 派单 → {agent_name}：{instruction[:160]}",
                        )
                        self._emit(
                            "dispatch",
                            {"agent": agent_name, "instruction": instruction},
                        )
                        _jadd(f"派 {agent_name}：{instruction[:300]}")
                        wr = self._run_worker(agent_name, instruction, _jtext(9000))
                        if wr.get("cancelled"):
                            interrupted = True
                            stop = True
                            break
                        result_text = wr.get("text", "") or "(无输出)"
                        _jadd(f"{agent_name} 结果：{result_text[:8000]}")
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.get("id"),
                                "content": result_text[:8000],
                            }
                        )
                        continue

                    # 未知工具
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.get("id"),
                            "content": f"未知工具：{name}",
                        }
                    )

                if stop:
                    break
            else:
                # 用满轮数仍没 finish
                final_text = (
                    final_text or "（已达最大调度轮数，暂停，请查看进度并指示下一步。）"
                )

            if interrupted:
                final_text = (
                    final_text or "（已被用户中断，已保存当前进度，可继续指示。）"
                )
                agent.add_turn(self.task_id, "system", "text", "⏹️ 任务已被用户中断")

            if final_text:
                agent.add_turn(self.task_id, "assistant", "text", final_text)
                self._emit("assistant", final_text)

            if await_confirm:
                agent.add_turn(
                    self.task_id,
                    "assistant",
                    "confirm_card",
                    json.dumps({"summary": final_text}, ensure_ascii=False),
                )
                self._emit("await_confirm", {"summary": final_text})

            if await_plan_approval:
                agent.add_turn(
                    self.task_id,
                    "assistant",
                    "plan_card",
                    json.dumps({"plan": final_text}, ensure_ascii=False),
                )
                self._emit("await_plan", {"plan": final_text})

            card = {}
            try:
                card = agent.update_checkpoint(self.task, final_text or "")
                self._emit("checkpoint", card)
            except Exception:
                pass

            status = (
                "已挂起"
                if interrupted
                else (
                    "待批准"
                    if await_plan_approval
                    else (
                        "待确认"
                        if await_confirm
                        else (
                            "等待输入"
                            if waiting_user
                            else ("已完成" if done else "等待输入")
                        )
                    )
                )
            )
            self.state.set(
                "status",
                "failed"
                if interrupted
                else (
                    "awaiting_plan"
                    if await_plan_approval
                    else (
                        "awaiting_confirm"
                        if await_confirm
                        else (
                            "waiting_user"
                            if waiting_user
                            else ("done" if done else "idle")
                        )
                    )
                ),
            )
            agent.update_task(
                self.task_id,
                status=status,
                progress=100
                if done
                else (
                    95
                    if await_confirm
                    else (
                        30
                        if await_plan_approval
                        else int((card or {}).get("progress", 10) or 10)
                    )
                ),
            )
            return {
                "final_text": final_text,
                "phase": self.state.get("phase"),
                "done": done,
                "waiting_user": waiting_user,
                "await_confirm": await_confirm,
                "interrupted": interrupted,
                "checkpoint": card,
            }

        except Exception as e:
            agent._log("Orchestrator 回合出错:", e)
            try:
                agent.add_turn(
                    self.task_id,
                    "system",
                    "text",
                    f"⚠️ 系统错误：{e}（已停在此处，可直接再发消息续跑）",
                )
            except Exception:
                pass
            try:
                agent.update_task(self.task_id, status="失败")
                self.state.set("status", "failed")
            except Exception:
                pass
            return {
                "final_text": f"系统错误：{e}",
                "phase": self.state.get("phase"),
                "done": False,
                "waiting_user": False,
                "await_confirm": False,
                "interrupted": True,
                "checkpoint": {},
            }
        finally:
            agent._running[self.task_id] = False


def run_coding_task(task_id, user_msg, on_event=None):
    """模块级入口，签名对齐 agent.run_agent_turn，供 server.py 网关调用。"""
    task = agent.get_task(task_id)
    if not task:
        raise ValueError(f"任务不存在: {task_id}")
    orch = CodingOrchestrator(task_id, task["workspace"])
    return orch.run_turn(user_msg, {"task": task, "on_event": on_event})
