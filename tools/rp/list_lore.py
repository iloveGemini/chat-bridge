# -*- coding: utf-8 -*-

from tools.utils import read_target_session
def get_schema():
    return {
        "type": "function",
        "function": {
            "name": "list_lore",
            "description": "列出某会话已有的世界书条目。",
            "parameters": {
                "type": "object",
                "properties": {"target_session_id": {"type": "string"}},
                "required": ["target_session_id"],
            },
        },
    }
def execute(args, context):
    try:
        _, scope = read_target_session(args)
        return {"entries": context["memory_store"].list_lore(scope)}
    except Exception as e: return {"error": str(e)}
