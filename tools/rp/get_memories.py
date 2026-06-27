# -*- coding: utf-8 -*-

from tools.utils import read_target_session

def get_schema():
    return {
        "type": "function",
        "function": {
            "name": "get_memories",
            "description": "获取指定会话下的所有录入记忆",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_session_id": {"type": "string", "description": "目标会话ID"},
                },
                "required": ["target_session_id"],
            },
        },
    }

def execute(args, context):
    try:
        _, scope = read_target_session(args)
        memories = context["memory_store"].get_memories(scope)
        return {"ok": True, "memories": memories}
    except Exception as e:
        return {"error": str(e)}
