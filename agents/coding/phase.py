# -*- coding: utf-8 -*-
"""Coding 的相位叶子 agent（plan/search/code/write/check）。

每个相位是一个注册的 BaseAgent 叶子节点：
  - 提示词经统一加载器 load_role_prompt(role) 取：用户覆盖优先，回落随程序发布的 md 默认
    （Task 11：用户可自定义覆盖、系统默认兜底，删不掉 md）。
  - run_phase() 是单一的「一相位执行」实现，orchestrator 与叶子 run() 共用，避免两份循环。

为「零行为变化」：默认（用户未覆盖）时 load_role_prompt 返回原 md 原文，build_phase_messages
拼装与原 orchestrator._phase_messages 逐字节一致。
"""
import json

import runtime.coding_runtime as agent
from core.paths import PROMPTS_DIR
from tools.registry import get_tools, execute_tool
from agents.engine import run_tool_loop
from agents.coding.roles import ROLE_PROMPTS
from agents.base import BaseAgent, AgentContext, AgentResult, register_agent

CODING_ROLES = ("planner", "searcher", "coder", "writer", "checker")


def _role_base_default(role):
    """该角色的「默认提示词」：旧版 data/prompts/coding/<role>.json 覆盖优先，否则随程序发布的 md。"""
    f = PROMPTS_DIR / "coding" / f"{role}.json"
    if f.exists():
        try:
            c = json.loads(f.read_text(encoding="utf-8")).get("content")
            if c is not None and c.strip():
                return c
        except Exception:
            pass
    return ROLE_PROMPTS.get(role, ROLE_PROMPTS["planner"])


def load_role_prompt(role):
    """统一加载：Agent 提示词预设(当前启用项) → 角色默认(.md/旧覆盖) 兜底。
    用户在「Agent 提示词」里给该角色选了自定义预设时，这里返回那份正文，
    从而经 build_phase_messages 注入系统提示词、真正传给模型。"""
    from prompts import agent_prompts as _ap
    return _ap.effective_prompt(role, _role_base_default(role))


def build_phase_messages(role, handoff, workspace_tree=""):
    sys_prompt = load_role_prompt(role)
    sys_full = (
        sys_prompt
        + "\n\n【环境】沙箱为 Windows，所有路径相对工作区根目录。"
        + (f"\n\n【工作区文件树】\n{workspace_tree}" if workspace_tree else "")
    )
    return [
        {"role": "system", "content": sys_full},
        {"role": "user", "content": handoff},
    ]


def run_phase(role, handoff, *, chat_fn, tool_ctx, task_id,
              emit=None, workspace_tree="", is_cancelled=None, max_rounds=6):
    """执行单个相位：拼提示词 → 跑「LLM↔工具」循环 → 返回相位结果 dict。
    返回： {"cancelled":True,"text":""} | {"clarify":True,"clarify_payload":..,"text":..} | {"text":..}
    （与原 orchestrator._run_phase 逐字节等价。）"""
    tools = get_tools(role)
    messages = build_phase_messages(role, handoff, workspace_tree)

    def _emit(kind, data):
        if emit:
            try:
                emit(kind, data)
            except Exception:
                pass

    def _on_content(content):
        # [NEED_USER] 是给编排器看的暂停标记，不展示给用户
        c = (content or "").replace("[NEED_USER]", "").strip()
        if c:
            agent.add_turn(task_id, "assistant", "text", f"[{role}] {c}")
            _emit("assistant", f"[{role}] {c}")

    def _on_tool_call(name, args):
        if name == "ask_user_clarification":
            return  # 由 _intercept 落「确认卡」turn，避免再冒一个工具调用气泡
        agent.add_turn(task_id, "assistant", "tool_call",
                       {"name": name, "args": args}, tool_name=name)
        _emit("tool_call", {"name": name, "args": args})

    def _intercept(name, args):
        if name == "ask_user_clarification":
            _emit("clarification", args)
            # 落成前端可交互渲染的确认卡 turn（ingestTurn 认 type=clarification_card，
            # content 是含 questions 的 JSON）。之前错存成 tool_result，所以前端看不见卡。
            agent.add_turn(task_id, "assistant", "clarification_card",
                           json.dumps(args, ensure_ascii=False), tool_name=name)
            return {"payload": args}
        return None

    def _on_tool_result(name, args, result, tc):
        result_str = json.dumps(result, ensure_ascii=False)
        agent.add_turn(task_id, "assistant", "tool_result", result_str, tool_name=name)
        _emit("tool_result", {"name": name, "result": result})

    out = run_tool_loop(
        messages=messages, tools=tools, max_rounds=max_rounds,
        chat=lambda m, t: chat_fn(m, t),
        execute=lambda name, args: execute_tool(name, args, tool_ctx),
        is_cancelled=is_cancelled,
        on_assistant_content=_on_content,
        on_tool_call=_on_tool_call,
        intercept=_intercept,
        on_tool_result=_on_tool_result,
    )

    stop = out.get("stop")
    content = (out.get("content") or "").strip()
    if stop == "cancelled":
        return {"cancelled": True, "text": ""}
    if stop == "intercepted":
        return {"clarify": True, "clarify_payload": out["intercept"]["payload"], "text": content}
    if stop == "no_tools":
        return {"text": content}
    return {"text": ""}


class CodingPhaseAgent(BaseAgent):
    """单相位叶子节点（统一接口）。run(ctx) 从 ctx.shared 取运行时依赖，供 FlowManager 路由。

    约定 ctx.shared 提供：chat_fn / tool_ctx / workspace_tree / is_cancelled / handoff(本相位入参)。
    输出写回 ctx.shared['{role}_text']；返回 AgentResult(status: done|need_user)。
    """
    def __init__(self, role):
        self.role = role
        self.agent_type = f"coding.{role}"

    def default_tool_grant(self):
        return ["coding"]

    def prompt_descriptor(self):
        # 每个 coding 相位角色都可被「Agent 提示词预设」管理，默认= 随程序发布的角色提示词
        return (self.role, ROLE_PROMPTS.get(self.role, ROLE_PROMPTS["planner"]))

    def run(self, ctx: AgentContext) -> AgentResult:
        sh = ctx.shared or {}
        res = run_phase(
            self.role, sh.get("handoff", ""),
            chat_fn=sh["chat_fn"], tool_ctx=sh.get("tool_ctx"),
            task_id=ctx.task_id, emit=ctx.on_event,
            workspace_tree=sh.get("workspace_tree", ""),
            is_cancelled=sh.get("is_cancelled"),
            max_rounds=sh.get("max_rounds", 6),
        )
        ctx.shared[f"{self.role}_text"] = res.get("text", "")
        if res.get("cancelled"):
            return AgentResult(status="need_user", output="", next_hint="cancelled")
        if res.get("clarify"):
            ctx.shared["clarify_payload"] = res.get("clarify_payload")
            return AgentResult(status="need_user", output=res.get("text", ""), next_hint="clarify")
        return AgentResult(status="done", output=res.get("text", ""))


for _r in CODING_ROLES:
    register_agent(CodingPhaseAgent(_r))
