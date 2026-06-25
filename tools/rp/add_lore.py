# -*- coding: utf-8 -*-

from tools.utils import read_target_session
def get_schema():
    return {
        "type": "function",
        "function": {
            "name": "add_lore",
            "description": "新增世界书条目",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_session_id": {"type": "string"},
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                    "keys": {"type": "array", "items": {"type": "string"}},
                    "always_on": {"type": "boolean"},
                    "priority": {"type": "integer"},
                },
                "required": ["target_session_id", "title", "content"],
            },
        },
    }
def execute(args, context):
    try:
        _, scope = read_target_session(args)
        if not (args.get("title") and args.get("content")): return {"error": "title/content必填"}
        emb = context["embed_cb"](args["title"], args["content"])
        rid = context["memory_store"].add_lore(
            scope, args["title"], args["content"],
            keys=args.get("keys") or [], priority=args.get("priority", 0),
            always_on=bool(args.get("always_on")), embedding=emb,
        )
        return {"ok": True, "id": rid}
    except Exception as e: return {"error": str(e)}
