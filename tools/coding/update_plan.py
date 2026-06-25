from agents.coding.state import CodingState

def get_schema():
    return {
        "type": "function",
        "function": {
            "name": "update_plan",
            "description": "更新任务的执行计划 (Todo 列表)。当你规划好任务步骤，或者完成某一步需要打勾时，调用此工具。前端会实时渲染这些 Todo。",
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "description": "完整的 Todo 列表。请包含所有步骤，已完成的设为 done: true。",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string", "description": "步骤描述"},
                                "done": {"type": "boolean", "description": "是否已完成"}
                            },
                            "required": ["text", "done"]
                        }
                    }
                },
                "required": ["todos"]
            }
        }
    }

def execute(args, context):
    todos = args.get("todos", [])
    if not isinstance(todos, list):
        return {"error": "todos 必须是数组"}
    
    workspace_dir = context.get("root_dir")
    if not workspace_dir:
        return {"error": "未找到 workspace_dir"}
        
    state = CodingState(workspace_dir)
    state.update_todos(todos)
    
    return {"ok": True, "msg": f"计划已更新，共 {len(todos)} 个步骤。"}
