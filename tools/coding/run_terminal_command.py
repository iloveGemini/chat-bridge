# -*- coding: utf-8 -*-
import os
import subprocess
import sys

def get_schema():
    return {
        "type": "function",
        "function": {
            "name": "run_terminal_command",
            "description": "在项目根目录下执行终端命令（运行测试、跑脚本、装包、构建等），返回 stdout/stderr/exit_code。默认超时 180 秒，装包/构建/大测试套件可用 timeout 调长。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的命令"},
                    "timeout": {"type": "integer", "description": "可选。超时秒数，默认 180，最大 1800。装包/构建可调大。"},
                },
                "required": ["command"],
            },
        },
    }

def execute(args, context):
    cmd = args.get("command")
    if not cmd:
        return {"error": "命令不能为空"}
    try:
        if sys.platform == "win32" and not cmd.strip().startswith("chcp"):
            cmd = f"chcp 65001 >nul & {cmd}"
        _to = min(1800, max(1, int(args.get("timeout") or 180)))
        _env = {**os.environ, **(context.get("env") or {})}
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=_to,
            cwd=str(context["root_dir"]), env=_env,
        )
        return {
            "ok": True, "command": cmd, "exit_code": result.returncode,
            "stdout": result.stdout[-3000:] if result.stdout else "",
            "stderr": result.stderr[-3000:] if result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"error": f"命令执行超时 ({_to}秒)，可传更大的 timeout 重试"}
    except Exception as e:
        return {"error": str(e)}
