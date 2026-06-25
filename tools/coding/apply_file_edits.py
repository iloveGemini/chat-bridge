# -*- coding: utf-8 -*-
def get_schema():
    return {
        "type": "function",
        "function": {
            "name": "apply_file_edits",
            "description": "【核心修改工具】对文件进行多处批量修改。传入包含多个修改块的数组，可以在同一次调用中完成文件的所有改动，极大避免重复思考。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {"type": "string", "description": "相对于项目根目录的文件路径。"},
                    "edits": {
                        "type": "array",
                        "description": "修改块列表，包含你想替换的多个区块。",
                        "items": {
                            "type": "object",
                            "properties": {
                                "start_line": {"type": "integer", "description": "要替换的起始行号（包含）"},
                                "end_line": {"type": "integer", "description": "要替换的结束行号（包含）"},
                                "new_content": {"type": "string", "description": "替换后的新代码内容。确保缩进正确。"},
                            },
                            "required": ["start_line", "end_line", "new_content"],
                        },
                    },
                },
                "required": ["filepath", "edits"],
            },
        },
    }

def execute(args, context):
    try:
        target_file = context["safe_resolve_cb"](args.get("filepath"))
        if not target_file.exists() or not target_file.is_file():
            return {"error": "文件不存在"}
        lines = target_file.read_text(encoding="utf-8").splitlines()
        edits = args.get("edits", [])
        edits_sorted = sorted(edits, key=lambda x: x.get("start_line", 0), reverse=True)
        for edit in edits_sorted:
            start_idx = max(1, edit.get("start_line", 1)) - 1
            end_idx = min(len(lines), edit.get("end_line", len(lines)))
            new_lines = edit.get("new_content", "").splitlines()
            lines = lines[:start_idx] + new_lines + lines[end_idx:]
        target_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {"ok": True, "msg": f"成功在 {target_file.name} 中应用了 {len(edits)} 处批量修改"}
    except ValueError as e:
        return {"error": str(e)}
