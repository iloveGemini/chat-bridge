# -*- coding: utf-8 -*-
def get_schema():
    return {
        "type": "function",
        "function": {
            "name": "glob_files",
            "description": "按通配符列出/查找文件（如 *.py、**/*.js、src/*）。用于了解目录结构、定位文件，避免盲读。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "通配符，如 *.py 或 **/*.js"},
                    "dirpath": {"type": "string", "description": "起始目录，默认 '.'"},
                },
                "required": ["pattern"],
            },
        },
    }

def execute(args, context):
    try:
        target_dir = context["safe_resolve_cb"](args.get("dirpath", "."))
        pattern = args.get("pattern")
        if not pattern: return {"error": "pattern 必填"}
        if not target_dir.exists() or not target_dir.is_dir(): return {"error": "目录不存在"}
        matches = list(target_dir.rglob(pattern.replace("**/", "")))
        max_results = 100
        items = [str(p.relative_to(context["root_dir"])) for p in matches[:max_results] if p.is_file()]
        res = {"pattern": pattern, "total_found": len(matches), "items": items}
        if len(matches) > max_results:
            res["truncated"] = True
            res["msg"] = f"结果已截断，仅展示前 {max_results} 个。"
        return res
    except Exception as e:
        return {"error": str(e)}
