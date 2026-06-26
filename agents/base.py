# -*- coding: utf-8 -*-
"""Agent 生态基座（ARCHITECTURE.md §5）：契约 + 注册表。

故意保持最小：只从手上已有的两个真实 agent（coding 编排器 + RP 聊天）归纳，
不为想象中的第三个 agent 预先抽象。四个插槽(prompt_assembler/context_provider/
tool_grant/output_schema)以可选属性形式预留，当前只把 tool_grant 落实（供 §4 统一 RBAC）。
"""
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class AgentContext:
    """在 agent 之间流转的状态信封（CodingState 的泛化）。"""
    agent_type: str = ""
    session_id: str = ""
    task_id: str = ""
    goal: str = ""
    user_msg: str = ""
    history: list = field(default_factory=list)
    shared: dict = field(default_factory=dict)        # 跨 agent 共享的自由状态
    on_event: Optional[Callable] = None


@dataclass
class AgentResult:
    """agent 交回给 Manager 的结果协议。manager 据此决策下一步。"""
    status: str = "done"               # done | need_handoff | need_user
    output: Any = None
    next_hint: Optional[str] = None     # need_handoff 时建议的下一个 agent_type / 事由


class BaseAgent:
    """所有 agent 的统一接口。子类至少实现 agent_type 与 run()。"""
    agent_type: str = "base"

    # 四插槽（预留；当前仅 tool_grant 落实）
    prompt_assembler = None
    context_provider = None
    output_schema = None

    def default_tool_grant(self) -> list:
        """该 agent_type 默认可用的工具名集合（§4 据此统一 RBAC）。"""
        return []

    def run(self, ctx: "AgentContext") -> "AgentResult":
        raise NotImplementedError


# ----------------- 注册表 -----------------
_AGENTS: dict = {}


def register_agent(agent: BaseAgent) -> BaseAgent:
    _AGENTS[agent.agent_type] = agent
    return agent


def get_agent(agent_type: str) -> Optional[BaseAgent]:
    return _AGENTS.get(agent_type)


def all_agent_types() -> list:
    return sorted(_AGENTS)
