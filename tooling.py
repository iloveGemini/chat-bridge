# -*- coding: utf-8 -*-
"""
聊天与代码核心工具库 (Tooling Module)
升级了“批处理”机制 (Batch/Patch)，大幅减少多轮工具调用导致的 429 限速问题。
"""

import json
import os
import re
import subprocess

# 区分哪些是代码工具，后续执行和鉴权时需要
CODING_TOOL_NAMES = {
    "read_file_with_lines",
    "apply_file_edits",
    "batch_write_files",
    "run_terminal_command",
    "grep_files",
    "glob_files",
    "replace_in_file",
}

PROMPT_CATEGORIES = ["main", "character", "user", "style", "post"]
PROTECTED_PROMPT_NAMES = {"default", "__assistant__"}


def _safe_name(name):
    """只允许字母数字下划线短横线，防止路径穿越"""
    return "".join(c for c in (name or "") if c.isalnum() or c in "-_")


def get_coding_tools():
    """代码编辑与检索工具 schema (升级为 Batch 批处理版)"""
    return [
        {
            "type": "function",
            "function": {
                "name": "read_file_with_lines",
                "description": "读取本地文件内容，自动在每行前加行号。大文件可用 offset/limit 只读取一段，避免撑爆上下文。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filepath": {
                            "type": "string",
                            "description": "相对于项目根目录的文件路径。",
                        },
                        "offset": {
                            "type": "integer",
                            "description": "可选。起始行号(0 基)，从第几行开始读，默认 0。",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "可选。最多读取多少行，默认读到文件末尾。",
                        },
                    },
                    "required": ["filepath"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "apply_file_edits",
                "description": "【核心修改工具】对文件进行多处批量修改。传入包含多个修改块的数组，可以在同一次调用中完成文件的所有改动，极大避免重复思考。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filepath": {
                            "type": "string",
                            "description": "相对于项目根目录的文件路径。",
                        },
                        "edits": {
                            "type": "array",
                            "description": "修改块列表，包含你想替换的多个区块。",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "start_line": {
                                        "type": "integer",
                                        "description": "要替换的起始行号（包含）",
                                    },
                                    "end_line": {
                                        "type": "integer",
                                        "description": "要替换的结束行号（包含）",
                                    },
                                    "new_content": {
                                        "type": "string",
                                        "description": "替换后的新代码内容。确保缩进正确。",
                                    },
                                },
                                "required": ["start_line", "end_line", "new_content"],
                            },
                        },
                    },
                    "required": ["filepath", "edits"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "batch_write_files",
                "description": "一次性新建或全量覆盖多个文件。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "files": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "filepath": {"type": "string"},
                                    "content": {"type": "string"},
                                },
                                "required": ["filepath", "content"],
                            },
                        }
                    },
                    "required": ["files"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_terminal_command",
                "description": "在项目根目录下执行终端命令（运行测试、跑脚本、装包、构建等），返回 stdout/stderr/exit_code。默认超时 180 秒，装包/构建/大测试套件可用 timeout 调长。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "要执行的命令"},
                        "timeout": {
                            "type": "integer",
                            "description": "可选。超时秒数，默认 180，最大 1800。装包/构建可调大。",
                        },
                    },
                    "required": ["command"],
                },
            },
        },
        # 👇 新增：全局内容搜索工具 (Grep)
        {
            "type": "function",
            "function": {
                "name": "grep_files",
                "description": "全局内容正则搜索工具。在大量文件中快速查找特定的关键字或代码片段，返回精准的匹配行及上下文，彻底告别盲目读取全量文件。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "要搜索的内容或正则表达式。",
                        },
                        "dirpath": {
                            "type": "string",
                            "description": "搜索起始目录，默认为 '.'",
                        },
                        "include_glob": {
                            "type": "string",
                            "description": "要过滤的文件模式，例如 **/*.py。默认 **/*",
                        },
                        "use_regex": {
                            "type": "boolean",
                            "description": "是否使用正则，默认 true。设为 false 则是字面量查找。",
                        },
                        "context_lines": {
                            "type": "integer",
                            "description": "匹配行前后显示的上下文行数，默认 2 行。",
                        },
                    },
                    "required": ["pattern"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "glob_files",
                "description": "按通配符列出/查找文件（如 *.py、**/*.js、src/*）。用于了解目录结构、定位文件，避免盲读。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "通配符，如 *.py 或 **/*.js"},
                        "dirpath": {"type": "string", "description": "起始目录，默认 '.'"},
                    },
                    "required": ["pattern"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "replace_in_file",
                "description": "【精确替换改文件】把文件中的 old_string 整段替换成 new_string，按内容定位而非行号，更稳。old_string 必须在文件中唯一（多处出现请加大上下文使其唯一，或设 replace_all=true 全替）。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filepath": {"type": "string", "description": "相对项目根的文件路径"},
                        "old_string": {"type": "string", "description": "要被替换的原文（含足够上下文以唯一定位）"},
                        "new_string": {"type": "string", "description": "替换后的新内容"},
                        "replace_all": {"type": "boolean", "description": "是否替换全部出现，默认 false（仅替换唯一一处）"},
                    },
                    "required": ["filepath", "old_string", "new_string"],
                },
            },
        },
    ]


def get_assistant_tools():
    """设定助手工具白名单"""
    target = {
        "target_session_id": {
            "type": "string",
            "description": "要配置的会话 id，来自 list_targets。",
        }
    }
    return [
        {
            "type": "function",
            "function": {
                "name": "list_targets",
                "description": "列出所有可配置的聊天会话（排除设定助手自己）。",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_lore",
                "description": "列出某会话已有的世界书条目。",
                "parameters": {
                    "type": "object",
                    "properties": dict(target),
                    "required": ["target_session_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "add_lore",
                "description": "新增世界书条目",
                "parameters": {
                    "type": "object",
                    "properties": dict(
                        target,
                        **{
                            "title": {"type": "string"},
                            "content": {"type": "string"},
                            "keys": {"type": "array", "items": {"type": "string"}},
                            "always_on": {"type": "boolean"},
                            "priority": {"type": "integer"},
                        },
                    ),
                    "required": ["target_session_id", "title", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "update_lore",
                "description": "更新世界书条目",
                "parameters": {
                    "type": "object",
                    "properties": dict(
                        target,
                        **{
                            "id": {"type": "integer"},
                            "title": {"type": "string"},
                            "content": {"type": "string"},
                            "keys": {"type": "array", "items": {"type": "string"}},
                            "always_on": {"type": "boolean"},
                            "priority": {"type": "integer"},
                        },
                    ),
                    "required": ["target_session_id", "id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "delete_lore",
                "description": "删除设定条目。",
                "parameters": {
                    "type": "object",
                    "properties": dict(target, **{"id": {"type": "integer"}}),
                    "required": ["target_session_id", "id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_prompts",
                "description": "列出某类提示词文件。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "enum": PROMPT_CATEGORIES}
                    },
                    "required": ["category"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_prompt",
                "description": "读取某个提示词文件的正文。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "enum": PROMPT_CATEGORIES},
                        "name": {"type": "string"},
                    },
                    "required": ["category", "name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "save_prompt",
                "description": "新建或更新提示词文件",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "enum": PROMPT_CATEGORIES},
                        "name": {"type": "string"},
                        "content": {"type": "string"},
                        "display_name": {"type": "string"},
                    },
                    "required": ["category", "name", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "bind_prompt",
                "description": "把提示词绑定到目标会话",
                "parameters": {
                    "type": "object",
                    "properties": dict(
                        target,
                        **{
                            "category": {
                                "type": "string",
                                "enum": ["character", "user"],
                            },
                            "name": {"type": "string"},
                        },
                    ),
                    "required": ["target_session_id", "category", "name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_presets",
                "description": "列出所有预设",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_preset",
                "description": "读取某个预设",
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "save_preset",
                "description": "保存预设包",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "main": {"type": "string"},
                        "style": {"type": "string"},
                        "post": {"type": "string"},
                    },
                    "required": ["name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "bind_preset",
                "description": "绑定预设到目标会话",
                "parameters": {
                    "type": "object",
                    "properties": dict(target, **{"name": {"type": "string"}}),
                    "required": ["target_session_id", "name"],
                },
            },
        },
        *get_coding_tools(),
    ]


def _read_target_session(args):
    sid = "".join(
        c for c in (args.get("target_session_id") or "") if c.isalnum() or c in "-_"
    )
    if not sid:
        raise ValueError("缺少 target_session_id")
    return sid, f"sess:{sid}"


def execute_tool(name, args, context):
    """
    统一工具派发接口。
    context 必须包含: root_dir, prompts_dir, sessions_dir, safe_resolve_cb, get_session_cb, memory_store, embed_cb
    """
    try:
        # 解包上下文
        memory_store = context["memory_store"]
        safe_resolve = context["safe_resolve_cb"]
        get_session = context["get_session_cb"]
        root_dir = context["root_dir"]
        prompts_dir = context["prompts_dir"]
        sessions_dir = context["sessions_dir"]

        # ========== 设定助手部分 ==========
        if name == "list_targets":
            out = []
            for d in sessions_dir.iterdir():
                if not d.is_dir():
                    continue
                s = get_session(d.name)
                if s.active_prompts.get("character") == "__assistant__":
                    continue
                char = s.active_prompts.get("character", "default")
                disp = char
                cfile = prompts_dir / "character" / f"{_safe_name(char)}.json"
                if cfile.exists():
                    try:
                        disp = json.loads(cfile.read_text(encoding="utf-8")).get(
                            "name", char
                        )
                    except Exception:
                        pass
                out.append(
                    {"session_id": d.name, "character": char, "character_name": disp}
                )
            return {"targets": out}

        if name == "list_lore":
            _, scope = _read_target_session(args)
            return {"entries": memory_store.list_lore(scope)}

        if name == "add_lore":
            _, scope = _read_target_session(args)
            if not (args.get("title") and args.get("content")):
                return {"error": "title/content必填"}
            emb = context["embed_cb"](args["title"], args["content"])
            rid = memory_store.add_lore(
                scope,
                args["title"],
                args["content"],
                keys=args.get("keys") or [],
                priority=args.get("priority", 0),
                always_on=bool(args.get("always_on")),
                embedding=emb,
            )
            return {"ok": True, "id": rid}

        if name == "update_lore":
            _, scope = _read_target_session(args)
            if not args.get("id"):
                return {"error": "id 必填"}
            emb = (
                context["embed_cb"](args.get("title"), args.get("content"))
                if (args.get("title") or args.get("content"))
                else None
            )
            ok = memory_store.update_lore(
                args["id"],
                title=args.get("title"),
                content=args.get("content"),
                keys=args.get("keys"),
                priority=args.get("priority"),
                always_on=args.get("always_on"),
                embedding=emb,
            )
            return {"ok": ok}

        if name == "delete_lore":
            _read_target_session(args)
            if not args.get("id"):
                return {"error": "id 必填"}
            return {"ok": memory_store.delete_lore(args["id"])}

        if name == "list_prompts":
            cat = args.get("category")
            if cat not in PROMPT_CATEGORIES:
                return {"error": f"category 须为 {PROMPT_CATEGORIES} 之一"}
            out = []
            cdir = prompts_dir / cat
            if cdir.exists():
                for f in sorted(cdir.glob("*.json")):
                    disp = f.stem
                    try:
                        disp = json.loads(f.read_text(encoding="utf-8")).get(
                            "name", f.stem
                        )
                    except Exception:
                        pass
                    out.append({"name": f.stem, "display_name": disp})
            return {"prompts": out}

        if name == "get_prompt":
            cat, nm = args.get("category"), _safe_name(args.get("name"))
            if cat not in PROMPT_CATEGORIES or not nm:
                return {"error": "参数不合法"}
            fpath = prompts_dir / cat / f"{nm}.json"
            if not fpath.exists():
                return {"error": "文件不存在"}
            d = json.loads(fpath.read_text(encoding="utf-8"))
            return {
                "name": nm,
                "display_name": d.get("name", nm),
                "content": d.get("content", ""),
            }

        if name == "save_prompt":
            cat, nm = args.get("category"), _safe_name(args.get("name"))
            if cat not in PROMPT_CATEGORIES or not nm:
                return {"error": "参数不合法"}
            if nm in PROTECTED_PROMPT_NAMES:
                return {"error": f"{nm} 是保留项"}
            file_data = {
                "name": args.get("display_name") or nm,
                "content": args.get("content", ""),
            }
            if cat == "character":
                file_data["avatar"] = args.get("avatar", "")
            (prompts_dir / cat).mkdir(parents=True, exist_ok=True)
            (prompts_dir / cat / f"{nm}.json").write_text(
                json.dumps(file_data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return {"ok": True, "name": nm}

        if name == "bind_prompt":
            sid, _ = _read_target_session(args)
            cat, nm = args.get("category"), _safe_name(args.get("name"))
            if cat not in ("character", "user") or not nm:
                return {"error": "参数不合法"}
            if not (prompts_dir / cat / f"{nm}.json").exists():
                return {"error": "文件不存在"}
            tgt = get_session(sid)
            with tgt.lock:
                tgt.active_prompts[cat] = nm
            tgt.save_active_prompts()
            return {"ok": True}

        if name == "list_presets":
            out = []
            presets_dir = prompts_dir / "_preset"
            if presets_dir.exists():
                for f in sorted(presets_dir.glob("*.json")):
                    try:
                        out.append(
                            {
                                "name": f.stem,
                                **json.loads(f.read_text(encoding="utf-8")),
                            }
                        )
                    except Exception:
                        out.append({"name": f.stem})
            return {"presets": out}

        if name == "get_preset":
            nm = _safe_name(args.get("name"))
            fpath = prompts_dir / "_preset" / f"{nm}.json"
            if not nm or not fpath.exists():
                return {"error": "预设不存在"}
            return {"name": nm, **json.loads(fpath.read_text(encoding="utf-8"))}

        if name == "save_preset":
            nm = _safe_name(args.get("name"))
            if not nm:
                return {"error": "name 必填"}
            if nm in PROTECTED_PROMPT_NAMES:
                return {"error": "保留项不可用"}
            preset = {
                k: _safe_name(args.get(k)) or "default"
                for k in ["main", "style", "post"]
            }
            pdir = prompts_dir / "_preset"
            pdir.mkdir(parents=True, exist_ok=True)
            (pdir / f"{nm}.json").write_text(
                json.dumps(preset, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return {"ok": True, "name": nm, "preset": preset}

        if name == "bind_preset":
            sid, _ = _read_target_session(args)
            nm = _safe_name(args.get("name"))
            if not nm or not (prompts_dir / "_preset" / f"{nm}.json").exists():
                return {"error": "预设不存在"}
            tgt = get_session(sid)
            with tgt.lock:
                tgt.active_prompts["preset"] = nm
            tgt.save_active_prompts()
            return {"ok": True}

        # ========== Coding Agent 代码工具部分 ==========
        if name == "glob_files":
            try:
                target_dir = safe_resolve(args.get("dirpath", "."))
                pattern = args.get("pattern")
                if not pattern:
                    return {"error": "pattern 必填"}
                if not target_dir.exists() or not target_dir.is_dir():
                    return {"error": "目录不存在"}

                # 使用 Python 的 rglob 查找
                matches = list(target_dir.rglob(pattern.replace("**/", "")))

                # 限制返回数量防止爆 Token
                max_results = 100
                items = [
                    str(p.relative_to(root_dir))
                    for p in matches[:max_results]
                    if p.is_file()
                ]

                res = {"pattern": pattern, "total_found": len(matches), "items": items}
                if len(matches) > max_results:
                    res["truncated"] = True
                    res["msg"] = f"结果已截断，仅展示前 {max_results} 个。"
                return res
            except Exception as e:
                return {"error": str(e)}

        if name == "grep_files":
            try:
                target_dir = safe_resolve(args.get("dirpath", "."))
                pattern = args.get("pattern")
                if not pattern:
                    return {"error": "pattern 必填"}
                if not target_dir.exists() or not target_dir.is_dir():
                    return {"error": "目录不存在"}

                include_glob = args.get("include_glob", "**/*").replace("**/", "")
                use_regex = args.get("use_regex", True)
                context_lines = max(
                    0, min(args.get("context_lines", 2), 5)
                )  # 上下文最多 5 行，防止撑爆

                regex = re.compile(pattern, re.IGNORECASE) if use_regex else None
                pattern_lower = pattern.lower() if not use_regex else None

                results = []
                max_file_results = 30  # 最多返回 30 个包含匹配项的文件
                total_matches = 0

                # 遍历目标文件
                for filepath in target_dir.rglob(include_glob):
                    if not filepath.is_file() or filepath.name.startswith("."):
                        continue
                    try:
                        # 仅处理能用 UTF-8 读取的文本文件
                        lines = filepath.read_text(encoding="utf-8").splitlines()
                    except UnicodeDecodeError:
                        continue

                    file_matches = []
                    for i, line in enumerate(lines):
                        is_match = False
                        if use_regex:
                            if regex.search(line):
                                is_match = True
                        else:
                            if pattern_lower in line.lower():
                                is_match = True

                        if is_match:
                            # 提取上下文
                            start_idx = max(0, i - context_lines)
                            end_idx = min(len(lines), i + context_lines + 1)

                            context_str = []
                            for ctx_i in range(start_idx, end_idx):
                                prefix = ">" if ctx_i == i else " "
                                safe_line = lines[ctx_i][:300] + (
                                    "..." if len(lines[ctx_i]) > 300 else ""
                                )
                                context_str.append(f"{prefix} {ctx_i + 1}: {safe_line}")

                            file_matches.append(
                                {"line": i + 1, "context": "\n".join(context_str)}
                            )
                            total_matches += 1

                    if file_matches:
                        results.append(
                            {
                                "filepath": str(filepath.relative_to(root_dir)),
                                # 每个文件最多展示前 5 处匹配，防止如 "import" 这种词导致霸屏
                                "matches": file_matches[:5],
                            }
                        )
                        if len(file_matches) > 5:
                            results[-1]["msg"] = (
                                f"该文件还有 {len(file_matches) - 5} 处匹配未展示"
                            )

                    if len(results) >= max_file_results:
                        break

                res = {
                    "pattern": pattern,
                    "matched_files_count": len(results),
                    "total_matches": total_matches,
                    "items": results,
                }
                if len(results) >= max_file_results:
                    res["truncated"] = True
                    res["msg"] = (
                        "命中文件数过多，已自动截断。建议增加 context_lines 或更换更精准的特征词。"
                    )
                return res
            except Exception as e:
                return {"error": str(e)}

        if name == "list_directory":
            try:
                target_dir = safe_resolve(args.get("dirpath", "."))
                if not target_dir.exists() or not target_dir.is_dir():
                    return {"error": "指定的目录不存在"}
                items = []
                for item in target_dir.iterdir():
                    if item.name.startswith(".") or item.name == "__pycache__":
                        continue
                    items.append(f"[{'DIR' if item.is_dir() else 'FILE'}] {item.name}")
                return {"dirpath": args.get("dirpath"), "items": items}
            except ValueError as e:
                return {"error": str(e)}

        if name == "read_file_with_lines":
            try:
                target_file = safe_resolve(args.get("filepath"))
                if not target_file.exists() or not target_file.is_file():
                    return {"error": "文件不存在"}
                lines = target_file.read_text(encoding="utf-8").splitlines()
                total = len(lines)
                start = max(0, int(args.get("offset") or 0))
                limit = args.get("limit")
                end = total if limit in (None, "", 0) else min(total, start + int(limit))
                sel = lines[start:end]
                numbered_content = "\n".join(
                    f"{str(start + i + 1).rjust(4)} | {line}" for i, line in enumerate(sel)
                )
                res = {
                    "filepath": args.get("filepath"),
                    "total_lines": total,
                    "content": numbered_content,
                }
                if start > 0 or end < total:
                    res["range"] = f"{start + 1}-{end} / {total}"
                return res
            except ValueError as e:
                return {"error": str(e)}

        if name == "apply_file_edits":
            try:
                target_file = safe_resolve(args.get("filepath"))
                if not target_file.exists() or not target_file.is_file():
                    return {"error": "文件不存在"}

                lines = target_file.read_text(encoding="utf-8").splitlines()
                edits = args.get("edits", [])

                # 安全策略：按 start_line 从后往前排序进行替换
                # 这样下方的修改不会影响上方修改的行号定位
                edits_sorted = sorted(
                    edits, key=lambda x: x.get("start_line", 0), reverse=True
                )

                for edit in edits_sorted:
                    start_idx = max(1, edit.get("start_line", 1)) - 1
                    end_idx = min(len(lines), edit.get("end_line", len(lines)))
                    new_lines = edit.get("new_content", "").splitlines()
                    lines = lines[:start_idx] + new_lines + lines[end_idx:]

                target_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
                return {
                    "ok": True,
                    "msg": f"成功在 {target_file.name} 中应用了 {len(edits)} 处批量修改",
                }
            except ValueError as e:
                return {"error": str(e)}

        if name == "batch_write_files":
            try:
                files = args.get("files", [])
                written = []
                for f_info in files:
                    target_file = safe_resolve(f_info.get("filepath"))
                    # 自动创建所需的父目录
                    target_file.parent.mkdir(parents=True, exist_ok=True)
                    target_file.write_text(f_info.get("content", ""), encoding="utf-8")
                    written.append(target_file.name)
                return {"ok": True, "msg": f"批量写入完成：{', '.join(written)}"}
            except ValueError as e:
                return {"error": str(e)}

        if name == "replace_in_file":
            try:
                target_file = safe_resolve(args.get("filepath"))
                if not target_file.exists() or not target_file.is_file():
                    return {"error": "文件不存在"}
                old = args.get("old_string")
                new = args.get("new_string")
                if old is None or new is None:
                    return {"error": "old_string / new_string 必填"}
                text = target_file.read_text(encoding="utf-8")
                cnt = text.count(old)
                if cnt == 0:
                    return {"error": "old_string 未在文件中找到，未做修改"}
                if cnt > 1 and not args.get("replace_all"):
                    return {
                        "error": f"old_string 出现 {cnt} 次，不唯一。请加大上下文使其唯一，或设 replace_all=true"
                    }
                if args.get("replace_all"):
                    text = text.replace(old, new)
                    done = cnt
                else:
                    text = text.replace(old, new, 1)
                    done = 1
                target_file.write_text(text, encoding="utf-8")
                return {"ok": True, "msg": f"已在 {target_file.name} 替换 {done} 处"}
            except ValueError as e:
                return {"error": str(e)}

        if name == "run_terminal_command":
            cmd = args.get("command")
            if not cmd:
                return {"error": "命令不能为空"}
            try:
                import sys

                if sys.platform == "win32" and not cmd.strip().startswith("chcp"):
                    cmd = f"chcp 65001 >nul & {cmd}"

                _to = min(1800, max(1, int(args.get("timeout") or 180)))
                _env = {**os.environ, **(context.get("env") or {})}
                result = subprocess.run(
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=_to,
                    cwd=str(root_dir),
                    env=_env,
                )
                return {
                    "ok": True,
                    "command": cmd,
                    "exit_code": result.returncode,
                    "stdout": result.stdout[-3000:] if result.stdout else "",
                    "stderr": result.stderr[-3000:] if result.stderr else "",
                }
            except subprocess.TimeoutExpired:
                return {"error": f"命令执行超时 ({min(1800, max(1, int(args.get('timeout') or 180)))}秒)，可传更大的 timeout 重试"}
            except Exception as e:
                return {"error": str(e)}

        return {"error": f"未知工具: {name}"}

    except Exception as e:
        return {"error": str(e)}
