# -*- coding: utf-8 -*-

def get_schema():
    return {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "列出指定目录下的文件和子目录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "dirpath": {
                        "type": "string",
                        "description": "要列出的目录路径，默认为当前目录 '.'"
                    }
                },
                "required": ["dirpath"]
            }
        }
    }

def execute(args, context):
    safe_resolve = context["safe_resolve_cb"]
    try:
        target_dir = safe_resolve(args.get("dirpath", "."))
        if not target_dir.exists() or not target_dir.is_dir():
            return {"error": "指定的目录不存在"}
        items = []
        for item in target_dir.iterdir():
            if item.name.startswith(".") or item.name == "__pycache__":
                continue
            items.append(f"[{'DIR' if item.is_dir() else 'FILE'}] {item.name}")
        return {"dirpath": args.get("dirpath", "."), "items": items}
    except Exception as e:
        return {"error": str(e)}
