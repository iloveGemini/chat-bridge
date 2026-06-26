# -*- coding: utf-8 -*-
from pathlib import Path
import re

def get_schema():
    return {
        "type": "function", "function": {
            "name": "smart_file_insight",
            "description": "高级文件洞察工具。一次性获取文件的总行数，并同时搜索多个正则模式。等效于在终端同时运行 wc -l 和多次 grep，是快速了解陌生大文件的最佳手段！",
            "parameters": {"type": "object", "properties": {
                "filepath": {"type": "string", "description": "要洞察的文件路径"},
                "patterns": {
                    "type": "array", 
                    "items": {"type": "string"}, 
                    "description": "要同时搜索的多个关键字或正则表达式列表。例如 ['思考|reasoning', 'timestamp|time']"
                }
            }, "required": ["filepath", "patterns"]}
        }
    }

def execute(args, context=None):
    filepath = args.get("filepath")
    patterns = args.get("patterns", [])
    
    if not isinstance(patterns, list):
        patterns = [patterns]
        
    target_file = Path(filepath).resolve()
    # 防目录穿越可以靠项目根目录限定，这里简单实现
    
    if not target_file.exists() or not target_file.is_file(): 
        return {"error": "文件不存在"}
    
    try:
        lines = target_file.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return {"error": "该文件无法以文本(UTF-8)格式读取"}
    
    total_lines = len(lines)
    out = [f"=== {target_file.name} 概览 ==="]
    out.append(f"总行数: {total_lines}")
    
    for pat in patterns[:5]:  # 防止 AI 发癫传太多词，最多并发搜 5 个
        out.append(f"\n=== 搜索 /{pat}/ ===")
        try:
            regex = re.compile(pat, re.IGNORECASE)
        except Exception:
            out.append("  [无效的正则表达式]")
            continue
            
        matches = []
        for i, line in enumerate(lines):
            if regex.search(line):
                # 去除前后空格，单行超长截断，防爆 Token
                safe_line = line.strip()[:200]
                matches.append(f"{str(i+1).rjust(4)} | {safe_line}")
            # 完美复刻 Claude 的 head -30 截断策略
            if len(matches) >= 30: 
                matches.append("  ... (结果过多，已强制截断前 30 行)")
                break
                
        if matches:
            out.extend(matches)
        else:
            out.append("  [未找到匹配项]")
            
    return {"result": "\n".join(out)}
