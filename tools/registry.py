# -*- coding: utf-8 -*-
import importlib
import pkgutil
from pathlib import Path

import tools.coding
import tools.rp
import tools.common

_REGISTRY = {}

def _load_package(pkg):
    pkg_path = Path(pkg.__file__).parent
    for _, module_name, _ in pkgutil.iter_modules([str(pkg_path)]):
        mod = importlib.import_module(f"{pkg.__name__}.{module_name}")
        if hasattr(mod, "get_schema") and hasattr(mod, "execute"):
            schema = mod.get_schema()
            name = schema["function"]["name"]
            _REGISTRY[name] = {
                "schema": schema,
                "execute": mod.execute,
                "category": pkg.__name__.split(".")[-1]
            }

_load_package(tools.coding)
_load_package(tools.rp)
_load_package(tools.common)

CODING_TOOL_NAMES = {name for name, meta in _REGISTRY.items() if meta["category"] == "coding"}

# 角色权限映射表 (RBAC)
# 角色权限映射表 (RBAC)
ROLE_PERMISSIONS = {
    # 规划者只规划/提问，不自己读文件——把"找资料"交给跑得快的 searcher(worker_api)，
    # 避免在主模型(rpm 低)上一轮轮瞎翻。需要资料就打 [NEED_SEARCH] 派侦察兵。
    "planner": ["ask_user_clarification", "update_plan", "add_workspace_file", "remove_workspace_file"],
    "searcher": ["read_file_with_lines", "grep_files", "glob_files", "get_outline", "get_function_code", "smart_file_insight"], # 侦察兵只能读
    "coder": [], # 纯写代码，不直接调工具
    "writer": ["apply_file_edits", "batch_write_files", "replace_in_file"], # 打字员只能写
    # developer = coder+writer 合并：能读侦察 + 直接落地修改
    "developer": ["read_file_with_lines", "grep_files", "glob_files", "get_outline",
                  "get_function_code", "smart_file_insight",
                  "apply_file_edits", "batch_write_files", "replace_in_file"],
    "checker": ["run_terminal_command"], # 测试员只能跑命令
    "rp_character": ["list_lore", "get_prompt"], # RP 角色只能读设定
    "gm": ["add_lore", "update_lore", "delete_lore", "add_memory", "get_memories", "rename_memory_folder", "delete_memory_folder", "set_memory_folder_resident"], # GM 可以改设定和记忆
    # 兼容老版本的全量权限
    "legacy_coding": [name for name, meta in _REGISTRY.items() if meta["category"] in ("coding", "common")],
    "legacy_assistant": [name for name, meta in _REGISTRY.items() if meta["category"] in ("rp", "coding", "common")]
}

def get_tools(role_name):
    """根据角色名称获取其有权限使用的工具 Schema 列表"""
    allowed_names = ROLE_PERMISSIONS.get(role_name, [])
    return [meta["schema"] for name, meta in _REGISTRY.items() if name in allowed_names]

# 兼容老代码的接口
def get_coding_tools():
    return get_tools("legacy_coding")

def get_assistant_tools():
    return get_tools("legacy_assistant")
def execute_tool(name, args, context):
    if name not in _REGISTRY:
        return {"error": f"未知工具: {name}"}
    try:
        return _REGISTRY[name]["execute"](args, context)
    except Exception as e:
        return {"error": str(e)}


# ============ §4 能力分组：agent 级工具授权（与会话级 toggle 统一） ============
# 模型：agent 按【能力组】授权(default_tool_grant 返回组名)，会话级 toggle(session/tools.py
# 的 SESSION_TOOL_KEYS)逐组开关；最终工具 = agent允许组 ∩ 会话启用组。
# 组名与 SESSION_TOOL_KEYS 对齐：outreach / coding / web。
# 注：coding 的【逐角色 RBAC】(ROLE_PERMISSIONS / get_tools(role)) 是 agent 内更细的一层，
#     与本组级授权正交，保持不变。
def _grp_outreach():
    from chat.outreach import _outreach_tool_defs, _outreach_enabled
    return _outreach_tool_defs() if _outreach_enabled() else []


def _grp_coding():
    return [meta["schema"] for name, meta in _REGISTRY.items() if meta["category"] == "coding"]


def _grp_web():
    return []  # 预留：联网检索工具，暂无


def _grp_memory():
    return [meta["schema"] for name, meta in _REGISTRY.items() if meta["category"] == "rp"]


TOOL_GROUPS = {
    "outreach": _grp_outreach,
    "coding": _grp_coding,
    "web": _grp_web,
    "memory": _grp_memory,
}


def resolve_tools(allowed_groups, enabled_toggles):
    """agent 允许的能力组 ∩ 会话启用的组 → 工具 schema 列表（按 TOOL_GROUPS 顺序、按名去重）。"""
    allowed = set(allowed_groups or [])
    toggles = enabled_toggles or {}
    out, seen = [], set()
    for g, prov in TOOL_GROUPS.items():
        if g in allowed and toggles.get(g):
            for sch in (prov() if callable(prov) else list(prov)):
                nm = (sch.get("function") or {}).get("name")
                if nm and nm not in seen:
                    seen.add(nm)
                    out.append(sch)
    return out
