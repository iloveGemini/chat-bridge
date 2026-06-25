# -*- coding: utf-8 -*-
def get_schema():
    return {
        "type": "function",
        "function": {
            "name": "read_file_with_lines",
            "description": "读取本地文件内容，自动在每行前加行号。大文件可用 offset/limit 只读取一段，避免撑爆上下文。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "相对于项目根目录的文件路径。"},
                    "offset": {"type": "integer", "description": "可选。起始行号(0 基)，从第几行开始读，默认 0。"},
                    "limit": {"type": "integer", "description": "可选。最多读取多少行，默认读到文件末尾。"},
                },
                "required": ["filepath"],
            },
        },
    }

def execute(args, context):
    try:
        target_file = context["safe_resolve_cb"](args.get("filepath"))
        if not target_file.exists() or not target_file.is_file():
            return {"error": "文件不存在"}
        lines = target_file.read_text(encoding="utf-8").splitlines()
        total = len(lines)
        start = max(0, int(args.get("offset") or 0))
        limit = args.get("limit")
        end = total if limit in (None, "", 0) else min(total, start + int(limit))
        sel = lines[start:end]
        numbered_content = "\n".join(f"{str(start + i + 1).rjust(4)} | {line}" for i, line in enumerate(sel))
        res = {"filepath": args.get("filepath"), "total_lines": total, "content": numbered_content}
        if start > 0 or end < total:
            res["range"] = f"{start + 1}-{end} / {total}"
        return res
    except ValueError as e:
        return {"error": str(e)}
