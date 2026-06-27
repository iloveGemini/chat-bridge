# -*- coding: utf-8 -*-
"""手动路由：解析开头 `@角色`，绕过 Manager Intake 直派该角色；`@auto` 交还自动。"""
import re

AUTO = "__auto__"

# @handle → 内部 role key（多别名）
HANDLES = {
    "@manager": "manager",
    "@architect": "architect",
    "@designer": "designer",
    "@context": "context_engineer",
    "@context_engineer": "context_engineer",
    "@programmer": "programmer",
    "@coder": "programmer",
    "@checker-tech": "checker_tech",
    "@checker_tech": "checker_tech",
    "@checker": "checker_tech",
    "@checker-design": "checker_design",
    "@checker_design": "checker_design",
    "@auto": AUTO,
}

# 给前端 @选择器用的展示列表（顺序固定）
PICKER = [
    ("@manager", "Manager · 路由/协调/验收"),
    ("@architect", "Architect · 技术方案"),
    ("@designer", "Designer · UI/UX"),
    ("@context", "Context Engineer · 收集/摘要"),
    ("@programmer", "Programmer · 写代码/测试"),
    ("@checker-tech", "Checker-Tech · 技术验收"),
    ("@checker-design", "Checker-Design · 设计验收"),
    ("@auto", "Auto · 交还 Manager 自动路由"),
]

_LEADING = re.compile(r"^\s*(@[A-Za-z_\-]+)\s*", re.UNICODE)


def parse_mention(text):
    """识别开头的 @handle。返回 (role_or_AUTO_or_None, remaining_text)。
    - 命中具体角色：(role_key, 去掉@后的正文)
    - 命中 @auto：(AUTO, 正文)
    - 未命中：(None, 原文)"""
    if not text:
        return None, text or ""
    m = _LEADING.match(text)
    if not m:
        return None, text
    handle = m.group(1).lower()
    role = HANDLES.get(handle)
    if role is None:
        return None, text  # 像 @ 但不是已知角色 → 当普通文本
    return role, text[m.end():].strip()
