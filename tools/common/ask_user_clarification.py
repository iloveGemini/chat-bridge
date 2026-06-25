def get_schema():
    return {
        "type": "function",
        "function": {
            "name": "ask_user_clarification",
            "description": "当需求不清晰时，向用户推送结构化的确认卡片。包含多个问题，每个问题可提供选项（可标记推荐）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "questions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "description": "问题ID"},
                                "text": {"type": "string", "description": "问题内容"},
                                "options": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "label": {"type": "string"},
                                            "value": {"type": "string"},
                                            "recommended": {"type": "boolean"}
                                        }
                                    }
                                },
                                "allow_custom": {"type": "boolean", "description": "是否允许自定义输入"}
                            },
                            "required": ["id", "text", "options"]
                        }
                    }
                },
                "required": ["questions"]
            }
        }
    }

def execute(args, context):
    # 这个工具的实际执行逻辑在 agent.py 的大循环里被特殊拦截并 emit 给前端
    # 这里只返回一个占位符，防止意外执行报错
    return {"msg": "已推送确认卡片给用户，等待用户回复。请暂停执行并等待。"}
