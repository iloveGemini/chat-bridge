# -*- coding: utf-8 -*-

from tools.utils import read_target_session

def get_schema():
    return {
        "type": "function",
        "function": {
            "name": "delete_memory_folder",
            "description": "删除指定记忆文件夹及其所有记录",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_session_id": {"type": "string", "description": "目标会话ID"},
                    "folder": {"type": "string", "description": "要删除的文件夹名称"},
                },
                "required": ["target_session_id", "folder"],
            },
        },
    }

def execute(args, context):
    try:
        _, scope = read_target_session(args)
        folder = args.get("folder")
        if folder is None:
            return {"error": "folder必填"}
        ok = context["memory_store"].delete_memory_folder(scope, folder)
        return {"ok": ok}
    except Exception as e:
        return {"error": str(e)}
