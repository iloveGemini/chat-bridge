# -*- coding: utf-8 -*-
def get_schema():
    return {
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
    }

def execute(args, context):
    try:
        files = args.get("files", [])
        written = []
        for f_info in files:
            target_file = context["safe_resolve_cb"](f_info.get("filepath"))
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_text(f_info.get("content", ""), encoding="utf-8")
            written.append(target_file.name)
        return {"ok": True, "msg": f"批量写入完成：{', '.join(written)}"}
    except ValueError as e:
        return {"error": str(e)}
