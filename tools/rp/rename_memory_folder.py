# -*- coding: utf-8 -*-

from tools.utils import read_target_session

def get_schema():
    return {
        "type": "function",
        "function": {
            "name": "rename_memory_folder",
            "description": "重命名记忆文件夹",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_session_id": {"type": "string", "description": "目标会话ID"},
                    "old_folder": {"type": "string", "description": "原文件夹名称"},
                    "new_folder": {"type": "string", "description": "新文件夹名称"},
                },
                "required": ["target_session_id", "old_folder", "new_folder"],
            },
        },
    }

def execute(args, context):
    try:
        _, scope = read_target_session(args)
        old_folder = args.get("old_folder")
        new_folder = args.get("new_folder")
        if old_folder is None or new_folder is None:
            return {"error": "old_folder和new_folder必填"}
        ok = context["memory_store"].rename_memory_folder(scope, old_folder, new_folder)
        return {"ok": ok}
    except Exception as e:
        return {"error": str(e)}
