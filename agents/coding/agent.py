# -*- coding: utf-8 -*-
"""CodingAgent —— 把 5 阶段 orchestrator 适配成统一 BaseAgent（不改其内部）。"""
from agents.base import BaseAgent, AgentContext, AgentResult, register_agent
from tools.registry import ROLE_PERMISSIONS

_CODING_ROLES = ("planner", "searcher", "coder", "writer", "checker")


class CodingAgent(BaseAgent):
    agent_type = "coding"

    def default_tool_grant(self) -> list:
        # coding 是多角色 agent：授权在 orchestrator 内按相位/角色分配(get_tools(role))。
        # 这里给出 agent 级【并集】，仅供 §4 统一 RBAC 时参考，不替代逐角色授权。
        names = set()
        for role in _CODING_ROLES:
            names.update(ROLE_PERMISSIONS.get(role, []))
        return sorted(names)

    def run(self, ctx: AgentContext) -> AgentResult:
        from agents.coding.orchestrator import run_coding_task  # 延迟导入
        res = run_coding_task(ctx.task_id, ctx.user_msg, on_event=ctx.on_event)
        if res.get("waiting_user"):
            status = "need_user"
        elif res.get("done"):
            status = "done"
        elif res.get("interrupted"):
            status = "need_user"
        else:
            status = "need_user"   # 跑完一轮未完成 → 交还用户继续指示
        return AgentResult(status=status, output=res.get("final_text"))


register_agent(CodingAgent())
