# -*- coding: utf-8 -*-
import json
from tools.utils import safe_name

def get_schema():
    return {
        "type": "function",
        "function": {
            "name": "list_targets",
            "description": "列出所有可配置的聊天会话（排除设定助手自己）。",
            "parameters": {"type": "object", "properties": {}},
        },
    }

def execute(args, context):
    try:
        out = []
        sessions_dir = context["sessions_dir"]
        prompts_dir = context["prompts_dir"]
        get_session = context["get_session_cb"]
        for d in sessions_dir.iterdir():
            if not d.is_dir(): continue
            s = get_session(d.name)
            if s.active_prompts.get("character") == "__assistant__": continue
            char = s.active_prompts.get("character", "default")
            disp = char
            cfile = prompts_dir / "character" / f"{safe_name(char)}.json"
            if cfile.exists():
                try: disp = json.loads(cfile.read_text(encoding="utf-8")).get("name", char)
                except Exception: pass
            out.append({"session_id": d.name, "character": char, "character_name": disp})
        return {"targets": out}
    except Exception as e:
        return {"error": str(e)}
