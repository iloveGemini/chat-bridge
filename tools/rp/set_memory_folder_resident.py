# -*- coding: utf-8 -*-

from tools.utils import read_target_session

def get_schema():
    return {
        "type": "function",
        "function": {
            "name": "set_memory_folder_resident",
            "description": "设置指定记忆文件夹的常驻状态",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_session_id": {"type": "string", "description": "目标会话ID"},
                    "folder": {"type": "string", "description": "文件夹名称"},
                    "is_resident": {"type": "integer", "description": "是否常驻(1为常驻，0为非常驻)"},
                },
                "required": ["target_session_id", "folder", "is_resident"],
            },
        },
    }

def execute(args, context):
    try:
        _, scope = read_target_session(args)
        folder = args.get("folder")
        is_resident = args.get("is_resident")
        if folder is None or is_resident is None:
            return {"error": "folder和is_resident必填"}
        ok = context["memory_store"].set_memory_folder_resident(scope, folder, is_resident)
        return {"ok": ok}
    except Exception as e:
        return {"error": str(e)}
