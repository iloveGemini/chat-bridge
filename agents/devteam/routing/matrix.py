# -*- coding: utf-8 -*-
"""Routing Matrix —— Manager Intake 的自动分流。

把用户请求粗分为 INFO/ARCH/DESIGN/BUILD/VERIFY/SCOPE，给出主 owner 与预置执行链。
这是给 Manager 的【建议】，不是硬规则：Manager 仍可在 Coordination 阶段调整。
"""
import re

# 七角色 key（Manager 自己不在派单 enum 里——它是派单者）
ALL_ROLES = ("manager", "architect", "designer", "context_engineer",
             "programmer", "checker_tech", "checker_design")
WORKER_ROLES = ("context_engineer", "architect", "designer", "programmer",
                "checker_tech", "checker_design")

# 预置执行链（文档 Routing Matrix）
CHAINS = {
    "new_page": ["context_engineer", "architect", "designer", "programmer", "checker_tech"],
    "new_button": ["programmer", "checker_tech"],
    "db_change": ["architect", "programmer", "checker_tech"],
    "ui_change": ["designer", "programmer", "checker_design"],
    "info": ["context_engineer"],
    "verify": ["checker_tech"],
    "default": ["context_engineer", "architect", "programmer", "checker_tech"],
}

# 关键词 → 类别（粗启发式；命中顺序从强到弱）
_RULES = [
    ("SCOPE", r"(范围|需求变更|改需求|重新定义|scope)"),
    ("VERIFY", r"(运行失败|报错|跑不起来|测试失败|验证|verify|bug|崩)"),
    ("DESIGN", r"(界面|布局|交互|样式|ui|ux|体验|页面长什么|配色|视觉)"),
    ("DB", r"(数据库|schema|表结构|sqlite|迁移|migration|建表|字段)"),
    ("ARCH", r"(架构|技术方案|api\b|接口设计|数据流|状态流|重构|结构)"),
    ("PAGE", r"(新页面|新增页面|加一个页面|new page|做个.*页)"),
    ("BUILD", r"(加|新增|实现|做一个|按钮|功能|写代码|改一下|修复|fix|实现)"),
    ("INFO", r"(查|找|了解|怎么实现|在哪|搜索|理解|看看|是什么)"),
]


def classify(text):
    """返回 {category, primary_owner, chain, chain_key}。"""
    t = (text or "").lower()
    cat = "BUILD"
    for name, pat in _RULES:
        if re.search(pat, t):
            cat = name
            break

    if cat == "INFO":
        return _route("INFO", "context_engineer", "info")
    if cat == "SCOPE":
        return _route("SCOPE", "manager", "default")
    if cat == "VERIFY":
        return _route("VERIFY", "checker_tech", "verify")
    if cat == "DESIGN":
        return _route("DESIGN", "designer", "ui_change")
    if cat == "DB":
        return _route("DB", "architect", "db_change")
    if cat == "ARCH":
        return _route("ARCH", "architect", "default")
    if cat == "PAGE":
        return _route("PAGE", "context_engineer", "new_page")
    # BUILD：默认偏「新按钮/小功能」最短链；Manager 可再展开
    return _route("BUILD", "programmer", "new_button")


def _route(category, owner, chain_key):
    return {
        "category": category,
        "primary_owner": owner,
        "chain_key": chain_key,
        "chain": CHAINS.get(chain_key, CHAINS["default"]),
    }
