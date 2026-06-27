# -*- coding: utf-8 -*-
"""六层项目状态树（ProjectState）的形状与默认值。

只存【事实状态】，不存记忆/上下文（Context Engineer 的产物进 devteam_context，不进这里）。
六层：project / requirements / architecture / design / implementation / verification。
"""

PHASES = ("intake", "planning", "design", "implementation", "verification", "release")


def default_state(task_id="", name=""):
    """一棵全新的项目状态树（phase 从 intake 起步）。"""
    return {
        "project": {
            "id": task_id,
            "name": name,
            "phase": "intake",
            "status": "active",            # active | blocked | completed
        },
        "requirements": {
            "current": "",
            "frozen": False,
            "changes": 0,
            "open_questions": [],
        },
        "architecture": {
            "structure": "",
            "api": "",
            "db": "",
            "approved": False,
            "debt": [],
        },
        "design": {
            "screens": [],
            "interaction": "",
            "states": [],
            "approved": False,
        },
        "implementation": {
            # feature_name -> {status, files, tests, owner}
            "features": {},
        },
        "verification": {
            "tech": {"pass": None, "findings": []},
            "design": {"pass": None, "findings": []},
            "manager": "",
        },
    }


# 顶层合法层名（用于 ROLE_WRITE 软校验、apply 的层定位）
LAYERS = ("project", "requirements", "architecture", "design", "implementation", "verification")
