# -*- coding: utf-8 -*-
"""主聊天链路：RP 三明治上下文拼装 + 原生 tool_calls 多轮循环 + 信封落库 + 记忆触发。"""
import json
import re
import threading
import time

from core.config import config, load_config as _load_config
from core.net import log_print, _http_post_json
from core.model_params import build_sampling
from core.paths import ROOT, PROMPTS_DIR, SESSIONS_DIR
from prompts.prompts import _get_display_name, _apply_macros
from prompts.assembler import PromptAssembler
from memory.memory import (
    build_injected_memory, _memory_cfg, _needs_summary, summarize_session, _lore_embedding,
)
from chat.envelope import ingest_reply
from chat.scene import _scene_stamp
from chat.outreach import _outreach_enabled, _outreach_tool_defs, _exec_outreach_tool
from session.session import get_session
from session.tools import get_session_tools
import tooling
import memory_store

API_REQUEST_TIMESTAMPS = []  # 滑动窗口限流：最近请求时间戳


def _safe_resolve_path(rel_path):
    """
    路径安全卫士：将相对路径转换为绝对路径，并严格限制在项目 ROOT 目录内。
    防止 AI 使用 '../../' 逃逸出项目文件夹。
    """
    target = (ROOT / str(rel_path)).resolve()
    # 检查解析后的目标路径是否以 ROOT 作为前缀
    if not str(target).startswith(str(ROOT.resolve())):
        raise ValueError(f"⚠️ 安全拦截：拒绝访问项目范围外的路径 ({rel_path})")
    return target


def call_llm_api(session_id):

    session = get_session(session_id)
    _load_config()
    api_cfg = config.get("api", {})

    with session.lock:
        session.is_typing = True
        session.typing_ts = time.time()
        session.current_status = "正在思考..."
        session.pending_event.set()
        recent_rounds = _memory_cfg().get("recent_rounds", 10)
        recent_msgs = [
            m
            for m in session.messages
            if m.get("type") not in ("reasoning", "tool_call", "tool_result")
        ][-(recent_rounds * 2) :]
        last_user_text = next(
            (
                m.get("text", "")
                for m in reversed(session.messages)
                if m.get("role") == "user"
            ),
            "",
        )

        char_name = _get_display_name(
            "character", session.active_prompts.get("character"), "AI助手"
        )
        user_name = _get_display_name(
            "user", session.active_prompts.get("user"), "用户"
        )

    # 1~3. 用 PromptAssembler 按槽位骨架组装（行为与原 build_header/tail 等价）
    asm = PromptAssembler(session)
    header_prompt = asm.build_system_head(char_name, user_name)
    memory_str = _apply_macros(
        build_injected_memory(session, last_user_text), char_name, user_name
    )
    tail_anchor = asm.build_tail(char_name, user_name, memory_str)

    # 4. 极其优雅地拼装三明治上下文
    api_messages = [{"role": "system", "content": header_prompt}]

    for i, m in enumerate(recent_msgs):
        is_last_msg = i == len(recent_msgs) - 1
        role = m["role"]
        raw_text = m.get("text", "")

        if m.get("image"):
            content_nodes = [
                {"type": "text", "text": (raw_text or "请看这张图片。")},
                {"type": "image_url", "image_url": {"url": m["image"]}},
            ]
            if is_last_msg and tail_anchor:
                content_nodes[0]["text"] += tail_anchor
            api_messages.append({"role": role, "content": content_nodes})
        else:
            content = raw_text
            if is_last_msg and tail_anchor:
                content = f"{content}{tail_anchor}"

            api_messages.append({"role": role, "content": content})

    url = f"{api_cfg.get('base_url', '').rstrip('/')}/chat/completions"
    payload = {
        "model": api_cfg.get("model", "deepseek-chat"),
        "messages": api_messages,
        **build_sampling(api_cfg, 0.7),
    }

    session.last_llm_payload = {
        "url": url,
        "model": payload["model"],
        "messages": api_messages,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    sys_preview = " ".join(header_prompt.split())[:40]
    last_u_preview = " ".join(last_user_text.split())[:40]
    mem_lines_count = len(memory_str.splitlines()) if memory_str else 0

    log_print(
        f"↗️ [LLM 请求][{session_id}] ── {payload['model']} ({len(api_messages)}条上下文)"
    )
    log_print(f"   ├─ System: {sys_preview}...")
    if mem_lines_count > 0:
        log_print(f"   ├─ 记忆块: 实时注入了 {mem_lines_count} 行长时上下文")
    log_print(f"   └─ User  : {last_u_preview}...")

    tools_cfg = get_session_tools(session_id)
    session_tools = []
    if tools_cfg.get("outreach") and _outreach_enabled():
        session_tools += _outreach_tool_defs()
    if tools_cfg.get("coding"):
        session_tools += tooling.get_coding_tools()
    if session_tools:
        payload["tools"] = session_tools
        payload["tool_choice"] = "auto"
        log_print(
            f"🔑 [工具授权][{session_id}] 本会话挂载: "
            f"{'主动联系 ' if (tools_cfg.get('outreach') and _outreach_enabled()) else ''}"
            f"{'本地项目操控' if tools_cfg.get('coding') else ''}".strip()
        )

    def _post(pl):
        global API_REQUEST_TIMESTAMPS

        MAX_RPM = 5  # 你的 API 限制（一分钟 5 次）
        WINDOW_SIZE = 60.0  # 窗口时间 60 秒

        now = time.time()
        # 清理 60 秒以前的过期记录
        API_REQUEST_TIMESTAMPS = [
            ts for ts in API_REQUEST_TIMESTAMPS if now - ts < WINDOW_SIZE
        ]

        # 检查频率是否超标
        if len(API_REQUEST_TIMESTAMPS) >= MAX_RPM:
            oldest_ts = API_REQUEST_TIMESTAMPS[0]
            wait_time = WINDOW_SIZE - (now - oldest_ts)

            if wait_time > 0:
                log_print(
                    f"⏳ [滑动窗口保护] 60秒内已发 {MAX_RPM} 次请求，强制挂起等待 {wait_time:.1f} 秒..."
                )
                time.sleep(wait_time + 0.5)  # 额外给0.5秒冗余
                now = time.time()

        # 记录本次请求时间
        API_REQUEST_TIMESTAMPS.append(now)

        return _http_post_json(
            url,
            pl,
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_cfg.get('api_key')}",
            },
            timeout=120,  # 防止排队时连接超时
            tag=f"聊天·{session_id}",
        )

    try:
        raw_reply = ""
        for _round in range(99):
            if session.interrupted:
                log_print(f"🚫 [LLM 请求][{session_id}] 已被用户中断")
                return
            res_data = _post(payload)
            if session.interrupted:
                log_print(f"🚫 [LLM 请求][{session_id}] 已被用户中断")
                return
            choice = res_data["choices"][0]["message"]
            tcs = choice.get("tool_calls") or []

            # 👇 [修改 2]：在这里拦截并打印 AI 的思考过程（上帝视角）
            ai_thought = choice.get("reasoning_content")
            if not ai_thought:
                ai_thought_match = re.search(
                    r"<think>(.*?)</think>", choice.get("content") or "", re.DOTALL
                )
                if ai_thought_match:
                    ai_thought = ai_thought_match.group(1)

            if ai_thought:
                # print(
                #    f"\n🧠 [AI 思考][{session_id}]:\n{ai_thought.strip()}\n" + "-" * 40
                # )
                with session.lock:
                    session.messages.append(
                        {
                            "role": "assistant",
                            "type": "reasoning",
                            "text": ai_thought.strip(),
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            **_scene_stamp(session),
                        }
                    )
                session.save_messages_async()

            if not tcs:
                raw_reply = (choice.get("content") or "").strip()
                break

            api_messages.append(
                {
                    "role": "assistant",
                    "content": choice.get("content") or "",
                    "tool_calls": tcs,
                }
            )
            for tc in tcs:
                fn = tc.get("function", {}) or {}
                try:
                    a = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    a = {}
                fname = fn.get("name", "")

                with session.lock:
                    session.current_status = f"正在调用工具: {fname}..."
                    session.messages.append(
                        {
                            "role": "assistant",
                            "type": "tool_call",
                            "tool_name": fname,
                            "tool_args": a,
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            **_scene_stamp(session),
                        }
                    )
                session.save_messages_async()
                # 👇 [修改 3]：在这里打印详细的工具调用参数
                print(f"⚙️  [调度工具]: {fname}")
                if fname == "run_terminal_command":
                    print(f"   > 执行命令: {a.get('command')}")
                elif fname == "apply_file_edits":
                    print(f"   > 修改文件: {a.get('filepath')}")
                    for idx, edit in enumerate(a.get("edits", [])):
                        print(
                            f"     [{idx + 1}] 替换 {edit.get('start_line')}~{edit.get('end_line')} 行"
                        )
                        new_text_preview = (
                            edit.get("new_content", "").splitlines()[0][:50]
                            if edit.get("new_content")
                            else ""
                        )
                        print(f"         + {new_text_preview}...")
                elif fname == "batch_write_files":
                    for f in a.get("files", []):
                        # print(f"   > 写入文件: {f.get('filepath')}")
                        pass
                elif fname == "grep_files":
                    print(f"   > 搜索内容: '{a.get('pattern')}'")
                elif fname == "read_file_with_lines":
                    print(f"   > 读取文件: {a.get('filepath')}")

                # ==========================
                # 👇 原有的工具派发执行区域 👇
                # ==========================
                if fname in tooling.CODING_TOOL_NAMES:
                    context = {
                        "root_dir": ROOT,
                        "prompts_dir": PROMPTS_DIR,
                        "sessions_dir": SESSIONS_DIR,
                        "safe_resolve_cb": _safe_resolve_path,
                        "get_session_cb": get_session,
                        "memory_store": memory_store,
                        "embed_cb": _lore_embedding,
                    }
                    r = tooling.execute_tool(fname, a, context)
                else:
                    r = _exec_outreach_tool(fname, a, session_id)
                # ==========================
                # 👆 原有的工具派发执行区域 👆
                # ==========================

                with session.lock:
                    session.messages.append(
                        {
                            "role": "assistant",
                            "type": "tool_result",
                            "tool_name": fname,
                            "text": json.dumps(r, ensure_ascii=False),
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            **_scene_stamp(session),
                        }
                    )
                session.save_messages_async()
                # ==========================
                # 👆 原有的工具派发执行区域 👆
                # ==========================

                api_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id"),
                        "content": json.dumps(r, ensure_ascii=False),
                    }
                )
            log_print(
                f"🛠️ [角色工具][{session_id}] 执行 {len(tcs)} 个 "
                f"({', '.join((t.get('function') or {}).get('name', '?') for t in tcs)})"
            )

            with session.lock:
                session.current_status = "正在思考..."

        # 解析信封：推进场景闩锁，只把干净 <content> 落库/发前端
        reply_text, _meta = ingest_reply(session, raw_reply)

        a_msg = {
            "role": "assistant",
            "text": reply_text,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            **_scene_stamp(session),
        }
        if _meta.get("emotion"):
            a_msg["emotion"] = _meta["emotion"]
        if _meta.get("voice_text"):
            a_msg["voice_text"] = _meta["voice_text"]
        with session.lock:
            session.messages.append(a_msg)
            session.is_typing = False
            session.current_status = ""
            session.pending_event.clear()
        session.save_messages_async()
        log_print(f"\n{'=' * 15} 🤖 [AI 回复 -> Session: {session_id}] {'=' * 15}")
        log_print(raw_reply)
        log_print(f"{'=' * 55}\n")

        if _needs_summary(session):
            log_print(f"🧠 [记忆触发] 增量总结 -> Session: {session_id}")
            threading.Thread(target=summarize_session, args=(session,)).start()

    except Exception as e:
        log_print(f"[API] 发生错误: {e}")
        with session.lock:
            session.messages.append(
                {
                    "role": "assistant",
                    "text": f"⚠️ 系统提示：{str(e)}",
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
            )
            session.is_typing = False
            session.current_status = ""
            session.pending_event.clear()
        session.save_messages_async()
