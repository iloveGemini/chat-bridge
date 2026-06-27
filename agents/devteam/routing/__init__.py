# -*- coding: utf-8 -*-
"""DevTeam 双路由：Manager 自动 Intake（matrix）+ @角色 手动（mention）。"""
from agents.devteam.routing.matrix import classify, CHAINS, WORKER_ROLES
from agents.devteam.routing.mention import parse_mention, HANDLES, AUTO

__all__ = ["classify", "CHAINS", "WORKER_ROLES", "parse_mention", "HANDLES", "AUTO"]
