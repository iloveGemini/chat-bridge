# -*- coding: utf-8 -*-

from tools.utils import read_target_session
def get_schema():
    return {
        "type": "function",
        "function": {
            "name": "delete_lore",
            "description": "删除设定条目。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_session_id": {"type": "string"},
                    "id": {"type": "integer"}
                },
                "required": ["target_session_id", "id"],
            },
        },
    }
def execute(args, context):
    try:
        read_target_session(args)
        if not args.get("id"): return {"error": "id 必填"}
        return {"ok": context["memory_store"].delete_lore(args["id"])}
    except Exception as e: return {"error": str(e)}
