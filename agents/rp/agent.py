# -*- coding: utf-8 -*-
"""RPChatAgent —— 把 RP 聊天主链路(call_llm_api)适配成统一 BaseAgent（不改其内部）。"""
from agents.base import BaseAgent, AgentContext, AgentResult, register_agent


class RPChatAgent(BaseAgent):
    agent_type = "rp"

    def default_tool_grant(self) -> list:
        # RP 工具按会话窗口动态授权(outreach/web/coding)；默认主动联系一组。
        # §4 会把「会话级 toggle」与「agent_type 默认授权」统一成一个模型。
        return ["schedule_outreach", "list_my_outreach", "cancel_outreach"]

    def run(self, ctx: AgentContext) -> AgentResult:
        from chat.llm import call_llm_api  # 延迟导入，避免基座加载期拉整条 chat 栈
        call_llm_api(ctx.session_id)
        return AgentResult(status="done")


register_agent(RPChatAgent())
