# -*- coding: utf-8 -*-

from tools.utils import read_target_session

def get_schema():
    return {
        "type": "function",
        "function": {
            "name": "add_memory",
            "description": "添加一条录入记忆",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_session_id": {"type": "string", "description": "目标会话ID"},
                    "content": {"type": "string", "description": "记忆内容"},
                    "folder": {"type": "string", "description": "所属文件夹名称"},
                    "is_resident": {"type": "integer", "description": "是否常驻(1为常驻，0为非常驻)"},
                },
                "required": ["target_session_id", "content"],
            },
        },
    }

def execute(args, context):
    try:
        _, scope = read_target_session(args)
        if not args.get("content"): return {"error": "content必填"}
        rid = context["memory_store"].add_memory(
            scope,
            args["content"],
            folder=args.get("folder", ""),
            is_resident=args.get("is_resident", 0)
        )
        return {"ok": True, "id": rid}
    except Exception as e:
        return {"error": str(e)}
