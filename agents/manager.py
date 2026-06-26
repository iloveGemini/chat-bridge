# -*- coding: utf-8 -*-
"""Agent Manager —— 统一递归接口（ARCHITECTURE.md §5）。

模型：Composite / Supervisor 树。每个节点都实现同一个 `run(ctx)->AgentResult`：
  - 叶子（Function/LLM agent）：直接干活。
  - Manager（组合）：持有一组子节点 + 负责它们之间的 status 流转；它本身也是 BaseAgent，
    所以能被上层 Manager 当成普通子节点再路由 —— "managers all the way down"。

流转自治：每一级 Manager 在自己的 run() 内把流转跑完，再把结果向上冒泡。
父节点不区分刚调的是叶子还是 Manager；流转状态是该级 run() 的局部变量，不是全树共读的全局态
（跨层要共享的数据走 AgentContext.shared）。

一级流转由三个钩子描述：
  entry(ctx)                       -> 首个步骤标识（None=无事可做）
  advance(ctx, last_step, result)  -> 下一步骤标识（None=本级结束）
  resolve(step, ctx)               -> 步骤标识 -> 可运行节点（默认走全局注册表；
                                      composite 可覆盖以返回绑定了特定预设的子 agent）

现用确定性死代码路由（StaticManager / 子类 FlowManager）；接口定死后，
以后换 LLMManager(BaseManager) 而叶子 agent 一行不用动。import 本模块即触发内置 agent 注册。
"""
from agents.base import (
    AgentContext, AgentResult, BaseAgent, get_agent, all_agent_types,
)

# import 适配器触发注册
from agents.coding.agent import CodingAgent  # noqa: F401
from agents.rp.agent import RPChatAgent      # noqa: F401


class BaseManager(BaseAgent):
    """组合节点：自治多跳。子类提供 entry/advance（必要时 resolve）。

    Manager 也是 BaseAgent（agent_type 默认空=匿名顶层路由器；作为子树时设具体 type 以便被上层路由）。
    """
    agent_type = ""

    # 步骤标识 -> 可运行节点。默认按 agent_type 查全局注册表；
    # composite 子树可覆盖，返回绑定了各自预设的子 agent。
    def resolve(self, step, ctx: AgentContext):
        return get_agent(step)

    # 进入这一级的首个步骤；None = 无事可做。
    def entry(self, ctx: AgentContext):
        raise NotImplementedError

    # 给定上一步及其结果，决定下一步；None = 本级流转结束。
    def advance(self, ctx: AgentContext, last_step, last_result: AgentResult):
        return None

    def run(self, ctx: AgentContext) -> AgentResult:
        step = self.entry(ctx)
        last = AgentResult(status="done")
        while step is not None:
            node = self.resolve(step, ctx)
            if node is None:
                return AgentResult(status="done", output=f"⚠️ 无法解析节点: {step!r}")
            last = node.run(ctx)                      # 叶子或子 Manager，统一调用
            if last.status == "need_user":            # 需用户介入：暂停整条链，向上冒泡
                return last
            step = self.advance(ctx, step, last)       # 本级自己决定下一跳；None=收尾
        return AgentResult(status="done", output=getattr(last, "output", None))

    # 兼容旧名：单次分派 = 跑一遍本级流转。
    def dispatch(self, ctx: AgentContext) -> AgentResult:
        return self.run(ctx)


class StaticManager(BaseManager):
    """顶层死代码路由：单跳。entry 选 ctx.agent_type（回落 default_type），advance 恒为 None。"""
    default_type = "rp"

    def entry(self, ctx: AgentContext):
        if ctx.agent_type and get_agent(ctx.agent_type):
            return ctx.agent_type
        return self.default_type if get_agent(self.default_type) else None

    def advance(self, ctx: AgentContext, last_step, last_result: AgentResult):
        return None  # 单跳：选中谁就跑谁，跑完即止


# ---------------- 顶层单例 + server 热路径入口 ----------------
# server 只把「功能参数」交给 Manager，由它路由到对应节点（Function 或子 Manager）。
MANAGER = StaticManager()


def run_rp(session_id):
    """RP 聊天主链路入口：经 Manager 路由到 rp 叶子（= call_llm_api）。"""
    return MANAGER.dispatch(AgentContext(agent_type="rp", session_id=session_id))


def run_coding(task_id, user_msg, on_event=None):
    """Coding 入口：经 Manager 路由到 coding 组合节点（= 5 阶段 orchestrator）。"""
    return MANAGER.dispatch(
        AgentContext(agent_type="coding", task_id=task_id, user_msg=user_msg, on_event=on_event)
    )
