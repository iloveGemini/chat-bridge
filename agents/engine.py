# -*- coding: utf-8 -*-
"""共享「LLM↔工具」多轮循环引擎。

RP 的 call_llm_api 与 coding 的 _run_phase 本质是同一套机制：
  反复调 LLM → 有 tool_calls 就逐个执行、把结果回灌 messages → 直到模型不再调工具。
机制相同、副作用各异（落库/事件/推理捕获/状态/拦截）。这里只固化「机制」，
所有副作用经回调注入，让两条链路共用同一引擎而行为不变（§5：别硬抹平副作用）。

run_tool_loop 会原地向 messages 追加 assistant(tool_calls) 与 tool(result) 消息，
与原两处实现一致。返回 dict：
  {"content": <最后一次模型正文>, "stop": "no_tools"|"cancelled"|"intercepted"|"max_rounds",
   "intercept": <intercept 回调返回值，仅 stop==intercepted>,
   "name"/"args": <被拦截的工具名/参数，仅 stop==intercepted>}
"""
import json


def run_tool_loop(
    *,
    messages,
    tools,
    chat,                      # (messages, tools) -> 原始响应 dict（含 choices[0].message）
    execute,                   # (name, args) -> 工具结果（任意可 json 序列化对象）
    max_rounds,
    is_cancelled=None,         # () -> bool，每轮开始前检查
    cancel_after_chat=False,   # True 则 chat() 之后再查一次（复现 RP 的 _post 后中断检查）
    extract_reasoning=None,    # (choice) -> str|None，抽推理链（RP 用）
    on_reasoning=None,         # (text) -> None
    on_assistant_content=None, # (content) -> None，每轮模型正文（含工具轮）
    on_tool_call=None,         # (name, args) -> None
    intercept=None,            # (name, args) -> 非 None 则中止循环并冒泡（如 ask_user_clarification）
    on_tool_result=None,       # (name, args, result, tool_call) -> None
    on_tools_done=None,        # (tool_calls) -> None，一轮所有工具执行完后（RP 用：日志+状态复位）
):
    content = ""
    for _round in range(max_rounds):
        if is_cancelled and is_cancelled():
            return {"content": content, "stop": "cancelled"}

        res = chat(messages, tools)

        if cancel_after_chat and is_cancelled and is_cancelled():
            return {"content": content, "stop": "cancelled"}

        choice = res["choices"][0]["message"]
        tcs = choice.get("tool_calls") or []
        content = choice.get("content") or ""

        if extract_reasoning is not None:
            rt = extract_reasoning(choice)
            if rt and on_reasoning is not None:
                on_reasoning(rt)

        if on_assistant_content is not None:
            on_assistant_content(content)

        if not tcs:
            return {"content": content, "stop": "no_tools"}

        messages.append(
            {"role": "assistant", "content": choice.get("content") or "", "tool_calls": tcs}
        )
        for tc in tcs:
            fn = tc.get("function", {}) or {}
            fname = fn.get("name", "")
            try:
                fargs = json.loads(fn.get("arguments") or "{}")
            except Exception:
                fargs = {}

            if on_tool_call is not None:
                on_tool_call(fname, fargs)

            if intercept is not None:
                sig = intercept(fname, fargs)
                if sig is not None:
                    return {"content": content, "stop": "intercepted",
                            "intercept": sig, "name": fname, "args": fargs}

            result = execute(fname, fargs)
            if on_tool_result is not None:
                on_tool_result(fname, fargs, result, tc)
            messages.append(
                {"role": "tool", "tool_call_id": tc.get("id"),
                 "content": json.dumps(result, ensure_ascii=False)}
            )

        if on_tools_done is not None:
            on_tools_done(tcs)

    return {"content": content, "stop": "max_rounds"}
