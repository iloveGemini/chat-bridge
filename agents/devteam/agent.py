# -*- coding: utf-8 -*-
"""DevTeamAgent —— 把 DevTeam 多角色编排器适配成统一 BaseAgent。

agent_type="devteam"，与通用 "coding" 组并存。import 本模块即注册。
"""
from agents.base import BaseAgent, AgentContext, AgentResult, register_agent


class DevTeamAgent(BaseAgent):
    agent_type = "devteam"

    def default_tool_grant(self) -> list:
        # 组级授权复用 coding 能力组；角色级 RBAC 由 phase.run_role / get_tools(role) 负责。
        return ["coding"]

    def run(self, ctx: AgentContext) -> AgentResult:
        from agents.devteam.orchestrator import run_devteam_task  # 延迟导入
        res = run_devteam_task(ctx.task_id, ctx.user_msg, on_event=ctx.on_event)
        status = "done" if res.get("done") else "need_user"
        return AgentResult(status=status, output=res.get("final_text"))


register_agent(DevTeamAgent())
