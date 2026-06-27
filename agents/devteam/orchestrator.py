# -*- coding: utf-8 -*-
"""DevTeamOrchestrator —— Manager Intake/Coordination 主循环 + 双路由 + 确认门。

- 自动路由：Manager（LLM）持 dispatch / advance_phase / finish / ask_user_clarification，
  按 Routing Matrix 建议分流；每收一份 REPORT 判 continue/rework/deliver。
- 手动路由：用户 @角色 → 跳过 Manager，直派该角色；@auto 交还自动。
- 单一写入口：所有状态变更经 ProjectStateStore.apply（角色提交 intent，这里统一落地）。
- 确认门：仅 phase_change / architecture.approved 三类检查点；开启「逐步确认」时升 plan_card 暂停，
  复用 /api/agent/approve_plan 续跑。
"""
import json

import runtime.coding_runtime as agent
import runtime.devteam_store as dstore
from agents.base import AgentContext, AgentResult
from agents.manager import BaseManager
from agents.devteam.phase import run_role
from agents.devteam.roles import ROLE_PROMPTS
from agents.devteam.state import ProjectStateStore, default_state
from agents.devteam.state.store import classify_checkpoint
from agents.devteam.messages import protocol, event_log
from agents.devteam.routing import classify, parse_mention, WORKER_ROLES, AUTO
from tools.registry import _REGISTRY
from agents.devteam.state.project_state import PHASES

ROLE_ENDPOINT = {
    "manager": "api", "architect": "api", "designer": "api",
    "context_engineer": "worker_api", "programmer": "api",
    "checker_tech": "worker_api", "checker_design": "worker_api",
}
ROLE_MAX_ROUNDS = {
    "context_engineer": 12, "architect": 10, "designer": 10,
    "programmer": 20, "checker_tech": 14, "checker_design": 10,
}
MAX_MANAGER_ROUNDS = 12


def _dispatch_tool():
    return {"type": "function", "function": {
        "name": "dispatch",
        "description": "派一个角色去做一件具体的事并拿回 REPORT。一次派一个。你自己不实现。",
        "parameters": {"type": "object", "properties": {
            "role": {"type": "string", "enum": list(WORKER_ROLES)},
            "instruction": {"type": "string", "description": "交给该角色的明确任务，尽量自包含"},
            "reason": {"type": "string", "description": "一句话说明为何此刻派它（作为旁白展示）"},
        }, "required": ["role", "instruction", "reason"]}}}


def _advance_phase_tool():
    return {"type": "function", "function": {
        "name": "advance_phase",
        "description": ("推进项目阶段（这是你唯一会写的状态）。合法转移："
                        "intake→planning→design→implementation→verification→release；"
                        "也可 implementation→design / verification→implementation 返工。每次必须给 reason。"),
        "parameters": {"type": "object", "properties": {
            "to": {"type": "string", "enum": list(PHASES)},
            "reason": {"type": "string"},
        }, "required": ["to", "reason"]}}}


def _finish_tool():
    return {"type": "function", "function": {
        "name": "finish",
        "description": "全部验收通过、可交付时调用，转用户确认。",
        "parameters": {"type": "object", "properties": {
            "summary": {"type": "string", "description": "交付小结"}},
            "required": ["summary"]}}}


def _manager_tools():
    tools = [_dispatch_tool(), _advance_phase_tool(), _finish_tool()]
    clar = _REGISTRY.get("ask_user_clarification")
    if clar:
        tools.append(clar["schema"])
    return tools


class DevTeamOrchestrator(BaseManager):
    agent_type = "devteam"

    def __init__(self, task_id, workspace_dir):
        self.task_id = task_id
        self.workspace_dir = workspace_dir
        self.task = None
        self.ctx = None
        self._on_event = None
        self.store = None

    # ---------------- 基础设施 ----------------
    def _chat_for(self, endpoint):
        def _fn(messages, tools):
            try:
                agent.set_last_prompt(self.task_id, messages, phase=self._phase())
            except Exception:
                pass
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

    def _confirm_checkpoints(self):
        try:
            return bool((agent.load_config().get("devteam", {}) or {}).get(
                "confirm_checkpoints", True))
        except Exception:
            return True

    def _phase(self):
        try:
            return self.store.snapshot()["project"]["phase"]
        except Exception:
            return "intake"

    def _state_brief(self):
        s = self.store.snapshot()
        p = s["project"]
        v = s["verification"]
        feats = ", ".join(s["implementation"]["features"].keys()) or "(无)"
        return (f"phase={p['phase']} status={p['status']} | "
                f"req.frozen={s['requirements']['frozen']} "
                f"arch.approved={s['architecture']['approved']} "
                f"design.approved={s['design']['approved']} | "
                f"features=[{feats}] | "
                f"verify.tech={v['tech']['pass']} verify.design={v['design']['pass']}")

    # ---------------- 状态落地 + 确认门 ----------------
    def _gate_or_apply(self, actor, op, label, journal):
        """返回 ('applied'|'gated'|'rejected', result)。命中检查点且开启确认时升门并暂停。"""
        cp = classify_checkpoint(op)
        if cp and self._confirm_checkpoints():
            dstore.set_kv(self.task_id, "pending_gate",
                          {"actor": actor, "op": op, "label": label, "checkpoint": cp})
            dstore.del_kv(self.task_id, "gate_approved")
            event_log.log(self.task_id, event_log.GATE_RAISED, actor=actor,
                          reason=f"{cp}: {label}")
            agent.add_turn(self.task_id, "assistant", "plan_card",
                           json.dumps({"plan": f"待确认状态变更（{cp}）：{label}"},
                                      ensure_ascii=False))
            self._emit("await_plan", {"plan": label})
            journal.append(f"⏸ 升起确认门（{cp}）：{label}")
            return "gated", None
        res = self.store.apply(actor, op)
        if res.get("ok"):
            journal.append(f"{actor} 状态变更：{label}")
            self._emit("state", {"actor": actor, "label": label})
            if res.get("warn"):
                agent.add_turn(self.task_id, "system", "text", f"⚠️ {res['warn']}")
                journal.append(f"⚠️ {res['warn']}")
            return "applied", res
        agent.add_turn(self.task_id, "system", "text", f"⛔ 状态变更被拒：{res.get('reason')}")
        journal.append(f"⛔ 拒绝：{res.get('reason')}")
        return "rejected", res

    def _apply_intents(self, actor, intents, journal):
        """落地一个角色提交的若干 intent。命中检查点则升门并返回 True（已暂停）。"""
        for it in intents:
            conf = it.get("confidence", 100)
            band = protocol.confidence_band(conf)
            if band == "block":
                protocol.persist(protocol.build_block(
                    self.task_id, actor, "manager",
                    missing="把握不足", why=f"confidence={conf}",
                    need="Manager 复核或补充信息", confidence=conf))
                journal.append(f"{actor} 低置信({conf})，记 BLOCK，转 Manager 复核")
            label = f"{actor}→{it.get('layer')} {json.dumps(it.get('patch'), ensure_ascii=False)[:80]}"
            status, _ = self._gate_or_apply(actor, it, label, journal)
            if status == "gated":
                return True
        return False

    # ---------------- 跑一个角色 ----------------
    def _run_role(self, role, instruction, journal):
        agent.add_turn(self.task_id, "system", "text", f"🧭 派单 → {role}：{instruction[:160]}")
        self._emit("dispatch", {"role": role, "instruction": instruction})
        event_log.log(self.task_id, event_log.ROLE_DISPATCHED, actor="manager",
                      reason=f"{role}: {instruction[:120]}")
        task_msg = protocol.build_task(self.task_id, "manager", role, goal=instruction)
        protocol.persist(task_msg)

        handoff = (f"【项目总目标】\n{self._goal()}\n\n【当前状态】\n{self._state_brief()}\n\n"
                   f"【你这次的任务】\n{instruction}\n\n"
                   f"【最近进展】\n{chr(10).join(journal[-8:]) if journal else '(无)'}")
        res = run_role(
            role, handoff,
            chat_fn=self._chat_for(ROLE_ENDPOINT.get(role, "api")),
            tool_ctx=self.ctx, task_id=self.task_id, emit=self._on_event,
            workspace_tree=self._workspace_tree(),
            is_cancelled=lambda: agent._check_cancel(self.task_id),
            max_rounds=ROLE_MAX_ROUNDS.get(role, 10),
        )
        text = res.get("text", "") or "(无输出)"
        intents = res.get("intents", []) or []
        conf = min([i.get("confidence", 100) for i in intents], default=100)
        protocol.persist(protocol.build_report(
            self.task_id, role, "manager", result=text[:4000], confidence=conf))
        gated = self._apply_intents(role, intents, journal)
        journal.append(f"{role} REPORT：{text[:300]}")
        return {"text": text, "gated": gated}

    def _goal(self):
        return (self.task or {}).get("goal", "") or (self.task or {}).get("title", "")

    # ---------------- 统一节点接口 ----------------
    def run(self, ctx: AgentContext) -> AgentResult:
        res = self.run_turn(ctx.user_msg, {"task": (ctx.shared or {}).get("task"),
                                           "on_event": ctx.on_event})
        status = "done" if res.get("done") else "need_user"
        return AgentResult(status=status, output=res.get("final_text"))

    # ---------------- 主入口 ----------------
    def run_turn(self, user_msg, context):
        context = context or {}
        self.task = context.get("task") or agent.get_task(self.task_id)
        if not self.task:
            raise ValueError(f"任务不存在: {self.task_id}")
        self._on_event = context.get("on_event")
        self.ctx = agent._sandbox_context(self.task)
        self.store = ProjectStateStore(self.task_id, name=self.task.get("title", ""))
        # 首次：物化默认状态树，前端可读
        if dstore.load_snapshot(self.task_id) is None:
            dstore.save_snapshot(self.task_id, default_state(self.task_id, self.task.get("title", "")))

        if agent._running.get(self.task_id):
            raise RuntimeError("该任务正在运行中")
        agent._running[self.task_id] = True
        with agent._cancel_lock:
            agent._cancel.discard(self.task_id)

        try:
            agent.update_task(self.task_id, status="运行中")
            journal = dstore.get_kv(self.task_id, "journal", []) or []

            if user_msg:
                agent.add_turn(self.task_id, "user", "text", user_msg)
                self._emit("user", user_msg)

            # 1) 处理挂起的确认门
            pending = dstore.get_kv(self.task_id, "pending_gate")
            approved = dstore.get_kv(self.task_id, "gate_approved", False)
            if pending:
                if approved and not user_msg:
                    res = self.store.apply(pending["actor"], pending["op"])
                    event_log.log(self.task_id, event_log.GATE_APPROVED,
                                  actor=pending["actor"], reason=pending.get("label", ""))
                    agent.add_turn(self.task_id, "system", "text",
                                   f"▶️ 已批准：{pending.get('label', '')}")
                    journal.append(f"用户已批准检查点：{pending.get('label', '')}")
                    dstore.del_kv(self.task_id, "pending_gate")
                    dstore.del_kv(self.task_id, "gate_approved")
                elif user_msg:
                    journal.append("用户改发新指示，放弃上一个挂起的确认门")
                    dstore.del_kv(self.task_id, "pending_gate")
                    dstore.del_kv(self.task_id, "gate_approved")

            # 2) 路由：手动 @角色 vs 自动
            route_role, body = parse_mention(user_msg) if user_msg else (None, "")
            if route_role == AUTO:
                event_log.log(self.task_id, event_log.ROUTE, actor="user", reason="@auto 交还自动路由")
                route_role = None

            if route_role:
                out = self._turn_manual(route_role, body, journal)
            else:
                out = self._turn_auto(user_msg, journal)

            dstore.set_kv(self.task_id, "journal", journal[-80:])
            return out
        except Exception as e:
            agent._log("DevTeam 回合出错:", e)
            try:
                agent.add_turn(self.task_id, "system", "text",
                               f"⚠️ 系统错误：{e}（已停在此处，可再发消息续跑）")
                agent.update_task(self.task_id, status="失败")
            except Exception:
                pass
            return {"final_text": f"系统错误：{e}", "done": False, "waiting_user": False,
                    "await_confirm": False, "interrupted": True, "phase": self._phase()}
        finally:
            agent._running[self.task_id] = False

    # ---------------- 手动路由：直派单个角色 ----------------
    def _turn_manual(self, role, body, journal):
        event_log.log(self.task_id, event_log.ROUTE, actor="user", reason=f"@{role} 手动直派")
        if role == "manager":
            # @manager 等价于交给自动协调
            return self._turn_auto(body, journal)
        r = self._run_role(role, body or "（未给具体指令，按你的职责对当前状态给出建议）", journal)
        final_text = r["text"]
        agent.add_turn(self.task_id, "assistant", "text", f"[{role}] {final_text}")
        self._emit("assistant", final_text)
        gated = r["gated"]
        self._finalize_status(waiting_user=not gated, await_state=gated)
        return {"final_text": final_text, "done": False, "waiting_user": not gated,
                "await_confirm": False, "await_state_approval": gated,
                "interrupted": False, "phase": self._phase()}

    # ---------------- 自动路由：Manager 协调循环 ----------------
    def _turn_auto(self, user_msg, journal):
        intake = classify(user_msg) if user_msg else None
        sys_full = (ROLE_PROMPTS["manager"]
                    + "\n\n【环境】沙箱 Windows，路径相对工作区根。"
                    + (f"\n\n【工作区文件树】\n{self._workspace_tree()}"
                       if self._workspace_tree() else ""))
        user0 = (f"【原始需求】\n{self._goal()}\n\n【当前项目状态】\n{self._state_brief()}")
        if intake:
            user0 += (f"\n\n【Intake 建议（可采纳可调整）】类别={intake['category']} "
                      f"主负责={intake['primary_owner']} 建议链={'→'.join(intake['chain'])}")
        if journal:
            user0 += "\n\n【项目进度记录（承接它继续，别重做）】\n" + "\n".join(journal[-30:])

        messages = [{"role": "system", "content": sys_full},
                    {"role": "user", "content": user0}]
        tools = _manager_tools()
        mgr_chat = self._chat_for("api")

        final_text = ""
        waiting_user = False
        await_confirm = False
        await_state = False
        interrupted = False

        for _round in range(MAX_MANAGER_ROUNDS):
            if agent._check_cancel(self.task_id):
                interrupted = True
                break
            res = mgr_chat(messages, tools)
            choice = (res.get("choices") or [{}])[0].get("message", {}) or {}
            content = (choice.get("content") or "").strip()
            tcs = choice.get("tool_calls") or []
            if content:
                agent.add_turn(self.task_id, "assistant", "text", f"[manager] {content}")
                self._emit("assistant", f"[manager] {content}")
                journal.append(f"经理：{content[:300]}")
            if not tcs:
                waiting_user = True
                final_text = content or "（需要你的进一步指示。）"
                break
            messages.append({"role": "assistant", "content": choice.get("content") or "",
                             "tool_calls": tcs})
            stop = False
            for tc in tcs:
                fn = tc.get("function", {}) or {}
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}

                if name == "ask_user_clarification":
                    agent.add_turn(self.task_id, "assistant", "clarification_card",
                                   json.dumps(args, ensure_ascii=False), tool_name=name)
                    self._emit("clarification", args)
                    waiting_user = True
                    final_text = "（已推送需求确认卡，等待你的选择/补充。）"
                    stop = True
                    break

                if name == "finish":
                    await_confirm = True
                    final_text = (args.get("summary") or "").strip() or "改动已完成，待你确认。"
                    stop = True
                    break

                if name == "advance_phase":
                    to = args.get("to", "")
                    reason = (args.get("reason") or "").strip()
                    op = {"kind": "phase", "to": to, "reason": reason}
                    status, r = self._gate_or_apply("manager", op, f"phase→{to}", journal)
                    if status == "gated":
                        await_state = True
                        final_text = f"即将推进阶段到 {to}，请确认。"
                        stop = True
                        break
                    feedback = (f"阶段已推进到 {to}" if status == "applied"
                                else f"阶段推进被拒：{r.get('reason')}")
                    messages.append({"role": "tool", "tool_call_id": tc.get("id"),
                                     "content": feedback})
                    continue

                if name == "dispatch":
                    role = args.get("role", "")
                    instruction = (args.get("instruction") or "").strip()
                    reason = (args.get("reason") or "").strip()
                    if reason:
                        agent.add_turn(self.task_id, "assistant", "text", f"[manager] {reason}")
                        self._emit("assistant", f"[manager] {reason}")
                        journal.append(f"经理：{reason[:200]}")
                    if role not in WORKER_ROLES:
                        messages.append({"role": "tool", "tool_call_id": tc.get("id"),
                                         "content": f"未知角色：{role}，可选 {list(WORKER_ROLES)}"})
                        continue
                    r = self._run_role(role, instruction, journal)
                    if r["gated"]:
                        await_state = True
                        final_text = f"{role} 提交的变更命中确认检查点，请确认。"
                        stop = True
                        break
                    messages.append({"role": "tool", "tool_call_id": tc.get("id"),
                                     "content": r["text"][:6000]})
                    continue

                messages.append({"role": "tool", "tool_call_id": tc.get("id"),
                                 "content": f"未知工具：{name}"})
            if stop:
                break
        else:
            final_text = final_text or "（已达最大调度轮数，暂停，请查看进度并指示下一步。）"

        if interrupted:
            final_text = final_text or "（已被用户中断，已保存进度，可继续指示。）"
            agent.add_turn(self.task_id, "system", "text", "⏹️ 任务已被用户中断")

        if final_text and not await_state:
            agent.add_turn(self.task_id, "assistant", "text", final_text)
            self._emit("assistant", final_text)

        if await_confirm:
            agent.add_turn(self.task_id, "assistant", "confirm_card",
                           json.dumps({"summary": final_text}, ensure_ascii=False))
            self._emit("await_confirm", {"summary": final_text})

        self._finalize_status(waiting_user=waiting_user, await_confirm=await_confirm,
                              await_state=await_state, interrupted=interrupted)
        return {"final_text": final_text, "done": False, "waiting_user": waiting_user,
                "await_confirm": await_confirm, "await_state_approval": await_state,
                "interrupted": interrupted, "phase": self._phase()}

    def _finalize_status(self, waiting_user=False, await_confirm=False,
                         await_state=False, interrupted=False):
        if interrupted:
            st = "已挂起"
        elif await_state:
            st = "待批准"
        elif await_confirm:
            st = "待确认"
        else:
            st = "等待输入"
        try:
            agent.update_task(self.task_id, status=st)
        except Exception:
            pass


def run_devteam_task(task_id, user_msg, on_event=None):
    """模块级入口，签名对齐 run_coding_task，供 manager.run_devteam 调用。"""
    task = agent.get_task(task_id)
    if not task:
        raise ValueError(f"任务不存在: {task_id}")
    orch = DevTeamOrchestrator(task_id, task["workspace"])
    return orch.run_turn(user_msg, {"task": task, "on_event": on_event})
