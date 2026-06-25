# -*- coding: utf-8 -*-
from tools.utils import py_outline, regex_outline

def get_schema():
    return {
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
    }

def execute(args, context):
    try:
        target_file = context["safe_resolve_cb"](args.get("filepath"))
        if not target_file.exists() or not target_file.is_file(): return {"error": "文件不存在"}
        src = target_file.read_text(encoding="utf-8")
        if target_file.suffix == ".py":
            symbols = py_outline(src)
            lang = "python"
        else:
            symbols = regex_outline(src)
            lang = (target_file.suffix.lstrip(".") or "?") + "(启发式)"
        return {"filepath": args.get("filepath"), "language": lang, "count": len(symbols), "symbols": symbols}
    except SyntaxError as e:
        return {"error": f"解析失败(语法错误): {e}"}
    except ValueError as e:
        return {"error": str(e)}
