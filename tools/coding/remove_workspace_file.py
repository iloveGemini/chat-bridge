import runtime.coding_runtime as agent

def get_schema():
    return {
        "type": "function",
        "function": {
            "name": "remove_workspace_file",
            "description": "将文件从工作区上下文中移除。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filepath": {
                        "type": "string",
                        "description": "要移除的文件路径"
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
    
    agent.remove_context(task_id, filepath)
    return {"result": f"Successfully removed {filepath} from workspace context."}
