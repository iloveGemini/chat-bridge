# -*- coding: utf-8 -*-
from tools.utils import py_locate, regex_locate

def get_schema():
    return {
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
    }

def execute(args, context):
    try:
        target_file = context["safe_resolve_cb"](args.get("filepath"))
        if not target_file.exists() or not target_file.is_file(): return {"error": "文件不存在"}
        want = (args.get("name") or "").strip()
        if not want: return {"error": "name 必填"}
        src = target_file.read_text(encoding="utf-8")
        lines = src.splitlines()
        if target_file.suffix == ".py":
            rng = py_locate(src, want)
        else:
            rng = regex_locate(lines, want)
        if not rng: return {"error": f"未找到 {want}（可先 get_outline 看可用名字）"}
        st, en = rng
        seg = lines[st - 1 : en]
        numbered = "\n".join(f"{str(st + i).rjust(4)} | {ln}" for i, ln in enumerate(seg))
        return {"filepath": args.get("filepath"), "name": want, "start_line": st, "end_line": en, "code": numbered}
    except SyntaxError as e:
        return {"error": f"解析失败: {e}"}
    except ValueError as e:
        return {"error": str(e)}
