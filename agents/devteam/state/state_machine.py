# -*- coding: utf-8 -*-
"""状态机：阶段转移图（双向 + reason 必填）+ 角色写层权限（软校验）。

文档要求阶段不绑死单向：implementation 发现缺设计可退回 design；verification 可返工到
implementation；都允许，但每次转移都必须带 reason（关键校验）。
"""

# 允许的阶段转移（白名单）。不在表里的转移一律拒绝。
TRANSITIONS = {
    "intake": ["planning"],
    "planning": ["design"],
    "design": ["implementation"],
    "implementation": ["design", "verification"],   # 双向：写代码发现缺设计可回退
    "verification": ["implementation", "release"],   # 返工 / 交付
    "release": [],
}


def can_transition(cur, to):
    return to in TRANSITIONS.get(cur, [])


# 角色 → 可写的状态层（软校验：越权记 WARN 事件但按轻量约定放行并标记）。
# Context Engineer 不在表里：它的产物进 devteam_context，绝不写 ProjectState（防记忆污染事实）。
ROLE_WRITE = {
    "manager": "project",            # 只推进 phase / status
    "architect": "architecture",
    "designer": "design",
    "programmer": "implementation",
    "checker_tech": "verification",  # 写 verification.tech
    "checker_design": "verification",  # 写 verification.design
}


def role_may_write(role, layer):
    """该角色是否被授权写这一层（软校验用）。"""
    return ROLE_WRITE.get(role) == layer
