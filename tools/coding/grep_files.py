# -*- coding: utf-8 -*-
import re

def get_schema():
    return {
        "type": "function",
        "function": {
            "name": "grep_files",
            "description": "全局内容正则搜索工具。在大量文件中快速查找特定的关键字或代码片段，返回精准的匹配行及上下文，彻底告别盲目读取全量文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "要搜索的内容或正则表达式。"},
                    "dirpath": {"type": "string", "description": "搜索起始目录，默认为 '.'"},
                    "include_glob": {"type": "string", "description": "要过滤的文件模式，例如 **/*.py。默认 **/*"},
                    "use_regex": {"type": "boolean", "description": "是否使用正则，默认 true。设为 false 则是字面量查找。"},
                    "context_lines": {"type": "integer", "description": "匹配行前后显示的上下文行数，默认 2 行。"},
                },
                "required": ["pattern"],
            },
        },
    }

def execute(args, context):
    try:
        target_dir = context["safe_resolve_cb"](args.get("dirpath", "."))
        pattern = args.get("pattern")
        if not pattern: return {"error": "pattern 必填"}
        if not target_dir.exists() or not target_dir.is_dir(): return {"error": "目录不存在"}
        include_glob = args.get("include_glob", "**/*").replace("**/", "")
        use_regex = args.get("use_regex", True)
        context_lines = max(0, min(args.get("context_lines", 2), 5))
        regex = re.compile(pattern, re.IGNORECASE) if use_regex else None
        pattern_lower = pattern.lower() if not use_regex else None
        results = []
        max_file_results = 30
        total_matches = 0
        for filepath in target_dir.rglob(include_glob):
            if not filepath.is_file() or filepath.name.startswith("."): continue
            try: lines = filepath.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError: continue
            file_matches = []
            for i, line in enumerate(lines):
                is_match = False
                if use_regex:
                    if regex.search(line): is_match = True
                else:
                    if pattern_lower in line.lower(): is_match = True
                if is_match:
                    start_idx = max(0, i - context_lines)
                    end_idx = min(len(lines), i + context_lines + 1)
                    context_str = []
                    for ctx_i in range(start_idx, end_idx):
                        prefix = ">" if ctx_i == i else " "
                        safe_line = lines[ctx_i][:300] + ("..." if len(lines[ctx_i]) > 300 else "")
                        context_str.append(f"{prefix} {ctx_i + 1}: {safe_line}")
                    file_matches.append({"line": i + 1, "context": "\n".join(context_str)})
                    total_matches += 1
            if file_matches:
                results.append({"filepath": str(filepath.relative_to(context["root_dir"])), "matches": file_matches[:5]})
                if len(file_matches) > 5: results[-1]["msg"] = f"该文件还有 {len(file_matches) - 5} 处匹配未展示"
            if len(results) >= max_file_results: break
        res = {"pattern": pattern, "matched_files_count": len(results), "total_matches": total_matches, "items": results}
        if len(results) >= max_file_results:
            res["truncated"] = True
            res["msg"] = "命中文件数过多，已自动截断。建议增加 context_lines 或更换更精准的特征词。"
        return res
    except Exception as e:
        return {"error": str(e)}
