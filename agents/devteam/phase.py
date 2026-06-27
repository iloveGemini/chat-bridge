# -*- coding: utf-8 -*-
"""DevTeam 单角色执行器 run_role()。

复用共享的 LLM↔工具循环引擎；在文件工具之外，给会写状态的角色注入两个【合成工具】：
  - submit_state(layer, patch, reason, confidence)：不直接写库，而是把【意图】收集起来，
    交回 orchestrator 经 ProjectStateStore.apply 统一落地（单一写入口 + 确认门在那层处理）。
  - save_context(key, kind, content)：Context Engineer 专用，直接写 devteam_context（非状态）。
角色的文本/工具调用照常落 turns，前端可见。
"""

import json

import runtime.coding_runtime as agent
import runtime.devteam_store as dstore
from agents.devteam.roles import ROLE_PROMPTS
from agents.engine import run_tool_loop
from tools.registry import execute_tool, get_tools

# 哪些角色可写状态（注入 submit_state）；context_engineer 注入 save_context
STATE_WRITERS = (
    "architect",
    "designer",
    "programmer",
    "checker_tech",
    "checker_design",
)


def _submit_state_tool():
    return {
        "type": "function",
        "function": {
            "name": "submit_state",
            "description": (
                "把你的结论写进项目状态树（经系统统一落地、留事件）。只写你被授权的那一层。"
                "patch 是要合并进该层的字段。reason 必填（说明为什么这么改）。"
                "confidence 0~100：你的把握度，≤30 表示别直接继续、会转 Manager 复核。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "layer": {
                        "type": "string",
                        "enum": [
                            "architecture",
                            "design",
                            "implementation",
                            "verification",
                        ],
                    },
                    "patch": {"type": "object", "description": "合并进该层的字段对象"},
                    "reason": {"type": "string"},
                    "confidence": {"type": "integer", "description": "0~100"},
                },
                "required": ["layer", "patch", "reason"],
            },
        },
    }


def _save_context_tool():
    return {
        "type": "function",
        "function": {
            "name": "save_context",
            "description": "把整理好的上下文/简报存入上下文缓存（非状态树）。供下游角色参考。",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "简短标识，如 'auth-brief'",
                    },
                    "kind": {"type": "string", "enum": ["brief", "summary"]},
                    "content": {"type": "string", "description": "整理后的事实性内容"},
                },
                "required": ["key", "content"],
            },
        },
    }


def _role_tools(role):
    tools = list(get_tools(role))
    if role == "context_engineer":
        tools.append(_save_context_tool())
    if role in STATE_WRITERS:
        tools.append(_submit_state_tool())
    return tools


def build_messages(role, handoff, workspace_tree=""):
    sys_prompt = ROLE_PROMPTS.get(role, "")
    sys_full = (
        sys_prompt
        + "\n\n【环境】沙箱为 Windows，所有路径相对工作区根目录。"
        + (f"\n\n【工作区文件树】\n{workspace_tree}" if workspace_tree else "")
    )
    return [
        {"role": "system", "content": sys_full},
        {"role": "user", "content": handoff},
    ]


def run_role(
    role,
    handoff,
    *,
    chat_fn,
    tool_ctx,
    task_id,
    emit=None,
    workspace_tree="",
    is_cancelled=None,
    max_rounds=8,
):
    """跑一个角色，返回 {text, stop, intents}。
    intents = 该角色提交的 submit_state 意图列表（orchestrator 负责经 store 落地 + 确认门）。"""
    tools = _role_tools(role)
    messages = build_messages(role, handoff, workspace_tree)
    intents = []
    findings = []

    def _emit(kind, data):
        if emit:
            try:
                emit(kind, data)
            except Exception:
                pass

    def _on_content(content):
        c = (content or "").strip()
        if c:
            agent.add_turn(task_id, "assistant", "text", f"[{role}] {c}")
            _emit("assistant", f"[{role}] {c}")

    def _on_tool_call(name, args):
        agent.add_turn(
            task_id,
            "assistant",
            "tool_call",
            {"name": name, "args": args},
            tool_name=name,
        )
        _emit("tool_call", {"name": name, "args": args})

    def _execute(name, args):
        if name == "submit_state":
            intent = {
                "kind": "layer",
                "layer": args.get("layer"),
                "patch": args.get("patch") or {},
                "reason": (args.get("reason") or "").strip(),
                "confidence": int(args.get("confidence", 100) or 100),
            }
            intents.append(intent)
            return {
                "ok": True,
                "recorded": True,
                "note": "意图已记录，将由系统统一落地（如命中检查点会先请用户确认）",
            }
        if name == "save_context":
            return dstore.save_context(
                task_id,
                args.get("key"),
                args.get("kind") or "brief",
                args.get("content") or "",
            )
        return execute_tool(name, args, tool_ctx)

    def _on_tool_result(name, args, result, tc):
        result_str = json.dumps(result, ensure_ascii=False)
        agent.add_turn(task_id, "assistant", "tool_result", result_str, tool_name=name)
        _emit("tool_result", {"name": name, "result": result})
        if name in (
            "read_file_with_lines",
            "grep_files",
            "glob_files",
            "get_outline",
            "get_function_code",
            "smart_file_insight",
        ):
            findings.append(
                f"# {name} {json.dumps(args, ensure_ascii=False)[:120]}\n{result_str[:2500]}"
            )

    out = run_tool_loop(
        messages=messages,
        tools=tools,
        max_rounds=max_rounds,
        chat=lambda m, t: chat_fn(m, t),
        execute=_execute,
        is_cancelled=is_cancelled,
        on_assistant_content=_on_content,
        on_tool_call=_on_tool_call,
        on_tool_result=_on_tool_result,
    )

    stop = out.get("stop")
    text = (out.get("content") or "").strip()
    # Context Engineer 的价值在它读到的原始内容，带给下游
    if role == "context_engineer" and findings:
        text = (
            (text + "\n\n" if text else "")
            + "【查到的原始内容】\n"
            + "\n\n".join(findings)[:9000]
        )
    return {"text": text, "stop": stop, "intents": intents}
