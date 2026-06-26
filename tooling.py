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
    "get_outline",
    "get_function_code",
}

PROMPT_CATEGORIES = ["main", "character", "user", "style", "post"]
PROTECTED_PROMPT_NAMES = {"default"}


def _safe_name(name):
    """只允许字母数字下划线短横线，防止路径穿越"""
    return "".join(c for c in (name or "") if c.isalnum() or c in "-_")


def _py_outline(src):
    """用 ast 解析 Python，返回类/函数/方法的结构（含行号、签名、首行 docstring）。"""
    import ast

    tree = ast.parse(src)
    out = []

    def doc1(node):
        d = ast.get_docstring(node)
        return (d or "").strip().split("\n")[0][:80]

    def walk(body, cls=None):
        for ch in body:
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                nm = f"{cls}.{ch.name}" if cls else ch.name
                a = ", ".join(x.arg for x in ch.args.args)
                out.append({
                    "type": "method" if cls else "function",
                    "name": nm, "signature": f"{ch.name}({a})",
                    "start_line": ch.lineno,
                    "end_line": getattr(ch, "end_lineno", ch.lineno),
                    "doc": doc1(ch),
                })
            elif isinstance(ch, ast.ClassDef):
                out.append({
                    "type": "class", "name": ch.name,
                    "signature": f"class {ch.name}",
                    "start_line": ch.lineno,
                    "end_line": getattr(ch, "end_lineno", ch.lineno),
                    "doc": doc1(ch),
                })
                walk(ch.body, ch.name)

    walk(tree.body)
    return out


def _py_locate(src, want):
    """在 Python 源里定位 want（可为 func 或 Class.method），返回 (start,end) 行号。"""
    import ast

    tree = ast.parse(src)
    cls_part, _, fn_part = want.partition(".")

    def search(body, cls=None):
        for ch in body:
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if fn_part:
                    if cls == cls_part and ch.name == fn_part:
                        return (ch.lineno, getattr(ch, "end_lineno", ch.lineno))
                elif ch.name == want:
                    return (ch.lineno, getattr(ch, "end_lineno", ch.lineno))
            elif isinstance(ch, ast.ClassDef):
                if not fn_part and ch.name == want:
                    return (ch.lineno, getattr(ch, "end_lineno", ch.lineno))
                r = search(ch.body, ch.name)
                if r:
                    return r
        return None

    return search(tree.body)


_KW_SKIP = {"if", "for", "while", "switch", "catch", "return", "function", "class", "else"}


def _regex_outline(src):
    """非 Python 的启发式大纲（正则，best-effort，只给起始行）。"""
    out = []
    pats = [
        (re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+(\w+)"), "class"),
        (re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)"), "function"),
        (re.compile(r"^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?\("), "function"),
        (re.compile(r"^\s*(\w+)\s*\([^)]*\)\s*\{"), "method"),
    ]
    for i, ln in enumerate(src.splitlines(), 1):
        for pat, kind in pats:
            m = pat.match(ln)
            if m and m.group(1) not in _KW_SKIP:
                out.append({
                    "type": kind, "name": m.group(1),
                    "signature": ln.strip()[:80],
                    "start_line": i, "end_line": i, "doc": "",
                })
                break
    return out


def _regex_locate(lines, want):
    """非 Python：找到声明行后用花括号配对求范围；无花括号则给个窗口。"""
    name_pat = re.compile(r"\b" + re.escape(want) + r"\b")
    for i, ln in enumerate(lines):
        if name_pat.search(ln) and ("(" in ln or "class" in ln or "=>" in ln):
            start = i + 1
            head = "".join(lines[i : i + 3])
            if "{" in head:
                depth = 0
                started = False
                for j in range(i, len(lines)):
                    depth += lines[j].count("{") - lines[j].count("}")
                    if "{" in lines[j]:
                        started = True
                    if started and depth <= 0:
                        return (start, j + 1)
            return (start, min(len(lines), start + 60))
    return None


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
        {
            "type": "function",
            "function": {
                "name": "get_outline",
                "description": "返回文件的结构大纲（类/函数/方法 + 行号 + 签名 + 首行 docstring），像 VSCode 大纲。先看大纲定位，再用 get_function_code 取具体函数，避免整文件读取。Python 用 AST 精确解析；其它语言为正则启发式。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filepath": {"type": "string", "description": "相对项目根的文件路径"}
                    },
                    "required": ["filepath"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_function_code",
                "description": "取出某个函数/方法的完整源码（带行号），方便你精确改它。name 可为函数名，或用 Class.method 指定类里的方法。配合 get_outline 用：先看大纲拿到名字，再取代码。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filepath": {"type": "string", "description": "相对项目根的文件路径"},
                        "name": {"type": "string", "description": "函数/方法名；类方法用 Class.method"},
                    },
                    "required": ["filepath", "name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "smart_file_insight",
                "description": "高级文件洞察工具。一次性获取文件的总行数，并同时搜索多个正则模式。等效于在终端同时运行 wc -l 和多次 grep，是快速了解陌生大文件的最佳手段！",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filepath": {"type": "string", "description": "要洞察的文件路径"},
                        "patterns": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "要同时搜索的多个关键字或正则表达式列表。例如 ['思考|reasoning', 'timestamp|time']"
                        }
                    },
                    "required": ["filepath", "patterns"]
                }
            }
        },
    ]


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

        if name == "get_outline":
            try:
                target_file = safe_resolve(args.get("filepath"))
                if not target_file.exists() or not target_file.is_file():
                    return {"error": "文件不存在"}
                src = target_file.read_text(encoding="utf-8")
                if target_file.suffix == ".py":
                    symbols = _py_outline(src)
                    lang = "python"
                else:
                    symbols = _regex_outline(src)
                    lang = (target_file.suffix.lstrip(".") or "?") + "(启发式)"
                return {
                    "filepath": args.get("filepath"),
                    "language": lang,
                    "count": len(symbols),
                    "symbols": symbols,
                }
            except SyntaxError as e:
                return {"error": f"解析失败(语法错误): {e}"}
            except ValueError as e:
                return {"error": str(e)}

        if name == "get_function_code":
            try:
                target_file = safe_resolve(args.get("filepath"))
                if not target_file.exists() or not target_file.is_file():
                    return {"error": "文件不存在"}
                want = (args.get("name") or "").strip()
                if not want:
                    return {"error": "name 必填（函数/方法名，类方法用 Class.method）"}
                src = target_file.read_text(encoding="utf-8")
                lines = src.splitlines()
                if target_file.suffix == ".py":
                    rng = _py_locate(src, want)
                else:
                    rng = _regex_locate(lines, want)
                if not rng:
                    return {"error": f"未找到 {want}（可先 get_outline 看可用名字）"}
                st, en = rng
                seg = lines[st - 1 : en]
                numbered = "\n".join(
                    f"{str(st + i).rjust(4)} | {ln}" for i, ln in enumerate(seg)
                )
                return {
                    "filepath": args.get("filepath"),
                    "name": want,
                    "start_line": st,
                    "end_line": en,
                    "code": numbered,
                }
            except SyntaxError as e:
                return {"error": f"解析失败: {e}"}
            except ValueError as e:
                return {"error": str(e)}

        if name == "smart_file_insight":
            try:
                target_file = safe_resolve(args.get("filepath"))
                if not target_file.exists() or not target_file.is_file(): 
                    return {"error": "文件不存在"}
                
                patterns = args.get("patterns", [])
                if not isinstance(patterns, list): patterns = [patterns]
                
                try:
                    lines = target_file.read_text(encoding="utf-8").splitlines()
                except UnicodeDecodeError:
                    return {"error": "该文件无法以文本(UTF-8)格式读取"}
                
                total_lines = len(lines)
                out = [f"=== {target_file.name} 概览 ==="]
                out.append(f"总行数: {total_lines}")
                
                import re
                for pat in patterns[:5]:
                    out.append(f"\n=== 搜索 /{pat}/ ===")
                    try:
                        regex = re.compile(pat, re.IGNORECASE)
                    except Exception:
                        out.append("  [无效的正则表达式]")
                        continue
                        
                    matches = []
                    for i, line in enumerate(lines):
                        if regex.search(line):
                            safe_line = line.strip()[:200]
                            matches.append(f"{str(i+1).rjust(4)} | {safe_line}")
                        if len(matches) >= 30: 
                            matches.append("  ... (结果过多，已强制截断前 30 行)")
                            break
                            
                    if matches:
                        out.extend(matches)
                    else:
                        out.append("  [未找到匹配项]")
                        
                return {"result": "\n".join(out)}
            except Exception as e: 
                return {"error": str(e)}

        return {"error": f"未知工具: {name}"}

    except Exception as e:
        return {"error": str(e)}
