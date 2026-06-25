# -*- coding: utf-8 -*-
def get_schema():
    return {
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
    }

def execute(args, context):
    try:
        target_file = context["safe_resolve_cb"](args.get("filepath"))
        if not target_file.exists() or not target_file.is_file(): return {"error": "文件不存在"}
        old = args.get("old_string")
        new = args.get("new_string")
        if old is None or new is None: return {"error": "old_string / new_string 必填"}
        text = target_file.read_text(encoding="utf-8")
        cnt = text.count(old)
        if cnt == 0: return {"error": "old_string 未在文件中找到，未做修改"}
        if cnt > 1 and not args.get("replace_all"):
            return {"error": f"old_string 出现 {cnt} 次，不唯一。请加大上下文使其唯一，或设 replace_all=true"}
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
