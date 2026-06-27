# -*- coding: utf-8 -*-
import os
import sys
import subprocess
import tempfile
def get_schema():
    return {
        "type": "function",
        "function": {
            "name": "run_playwright_script",
            "description": "执行基于 Playwright 的 Python 脚本，用于操作网页并返回执行结果。",
            "parameters": {
                "type": "object",
                "properties": {
                    "script": {
                        "type": "string",
                        "description": "Python Playwright 脚本代码字符串。"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "脚本执行超时时间（秒），默认 30 秒。"
                    }
                },
                "required": ["script"]
            }
        }
    }

def execute(args, context):
    script = args.get("script", "")
    timeout = args.get("timeout", 30)
    
    python_exe = sys.executable
    if os.path.exists("venv/Scripts/python.exe"):
        python_exe = "venv/Scripts/python.exe"
    elif os.path.exists("venv/bin/python"):
        python_exe = "venv/bin/python"
        
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w", encoding="utf-8") as f:
        f.write(script)
        tmp_file = f.name
        
    try:
        result = subprocess.run(
            [python_exe, tmp_file],
            capture_output=True,
            text=True,
            timeout=timeout
        )
        
        output = result.stdout
        if result.stderr:
            output += "\n[stderr]\n" + result.stderr
            
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
            "output": output.strip()
        }
    except subprocess.TimeoutExpired as e:
        stdout_str = e.stdout.decode('utf-8', errors='replace') if e.stdout else ""
        stderr_str = e.stderr.decode('utf-8', errors='replace') if e.stderr else ""
        return {"error": f"执行超时 ({timeout}s)", "output": stdout_str + "\n" + stderr_str}
    except Exception as e:
        return {"error": str(e)}
    finally:
        if os.path.exists(tmp_file):
            try:
                os.remove(tmp_file)
            except:
                pass
