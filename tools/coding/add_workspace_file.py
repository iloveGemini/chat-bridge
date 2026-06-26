import runtime.coding_runtime as agent

def get_schema():
    return {
        "type": "function",
        "function": {
            "name": "add_workspace_file",
            "description": "将文件添加到工作区上下文中，供后续任务使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "要添加的文件路径"
                    }
                },
                "required": ["filepath"]
            }
        }
    }

def execute(args, context):
    task_id = context.get("task_id")
    filepath = args.get("filepath")
    if not task_id or not filepath:
        return {"error": "Missing task_id or filepath"}
    
    agent.add_context(task_id, filepath, "full")
    return {"result": f"Successfully added {filepath} to workspace context."}
