# -*- coding: utf-8 -*-
"""RPChatAgent —— 把 RP 聊天主链路(call_llm_api)适配成统一 BaseAgent（不改其内部）。"""
from agents.base import BaseAgent, AgentContext, AgentResult, register_agent


class RPChatAgent(BaseAgent):
    agent_type = "rp"

    def default_tool_grant(self) -> list:
        # 能力组授权：RP 允许全部会话可 toggle 的组；具体开关由 session 的 tools.json 决定。
        from session.tools import SESSION_TOOL_KEYS
        return list(SESSION_TOOL_KEYS)

    def run(self, ctx: AgentContext) -> AgentResult:
        from chat.llm import call_llm_api  # 延迟导入，避免基座加载期拉整条 chat 栈
        call_llm_api(ctx.session_id)
        return AgentResult(status="done")


register_agent(RPChatAgent())
