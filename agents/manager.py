# -*- coding: utf-8 -*-
"""Agent Manager —— 本地优先、留扩展（ARCHITECTURE.md §5）。

现用确定性死代码路由 StaticManager；接口与信封(AgentContext/AgentResult)定死后，
以后可换 LLMManager(BaseManager) 而 agent 一行不用动。
import 本模块即触发内置 agent 注册。
"""
from agents.base import AgentContext, AgentResult, get_agent, all_agent_types

# import 适配器触发注册
from agents.coding.agent import CodingAgent  # noqa: F401
from agents.rp.agent import RPChatAgent      # noqa: F401


class BaseManager:
    def route(self, ctx: AgentContext) -> str:
        """决定下一个执行的 agent_type；返回 '' 表示结束/无路由。"""
        raise NotImplementedError

    def dispatch(self, ctx: AgentContext) -> AgentResult:
        at = self.route(ctx)
        agent = get_agent(at)
        if agent is None:
            return AgentResult(status="done", output=f"⚠️ 无可路由的 agent: {at!r}")
        return agent.run(ctx)


class StaticManager(BaseManager):
    """死代码路由：ctx.agent_type 命中已注册 agent 就直选，否则回落 default_type。"""
    default_type = "rp"

    def route(self, ctx: AgentContext) -> str:
        if ctx.agent_type and get_agent(ctx.agent_type):
            return ctx.agent_type
        return self.default_type if get_agent(self.default_type) else ""
