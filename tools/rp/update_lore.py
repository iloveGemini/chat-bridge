# -*- coding: utf-8 -*-

from tools.utils import read_target_session
def get_schema():
    return {
        "type": "function",
        "function": {
            "name": "update_lore",
            "description": "更新世界书条目",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_session_id": {"type": "string"},
                    "id": {"type": "integer"},
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                    "keys": {"type": "array", "items": {"type": "string"}},
                    "always_on": {"type": "boolean"},
                    "priority": {"type": "integer"},
                },
                "required": ["target_session_id", "id"],
            },
        },
    }
def execute(args, context):
    try:
        _, scope = read_target_session(args)
        if not args.get("id"): return {"error": "id 必填"}
        emb = context["embed_cb"](args.get("title"), args.get("content")) if (args.get("title") or args.get("content")) else None
        ok = context["memory_store"].update_lore(
            args["id"], title=args.get("title"), content=args.get("content"),
            keys=args.get("keys"), priority=args.get("priority"),
            always_on=args.get("always_on"), embedding=emb,
        )
        return {"ok": ok}
    except Exception as e: return {"error": str(e)}
