# -*- coding: utf-8 -*-
"""DevTeam 角色提示词载入器。

每个角色的系统提示词 = 该角色 md + 共享 global_rules.md（拼在末尾）。
从 prompts/*.md 读取，集中成 ROLE_PROMPTS 供 phase / orchestrator 使用。
"""
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

ROLES = ("manager", "architect", "designer", "context_engineer",
         "programmer", "checker_tech", "checker_design")


def _read(name):
    f = _PROMPTS_DIR / f"{name}.md"
    try:
        return f.read_text(encoding="utf-8")
    except Exception:
        return ""


_GLOBAL = _read("global_rules")

ROLE_PROMPTS = {
    r: (_read(r) + "\n\n" + _GLOBAL).strip() for r in ROLES
}
