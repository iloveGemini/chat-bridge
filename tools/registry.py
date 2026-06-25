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

# 角色权限映射表 (RBAC)
ROLE_PERMISSIONS = {
    "planner": ["ask_user_clarification", "update_plan"], # 规划者可以提问和更新计划
    "searcher": ["read_file_with_lines", "grep_files", "glob_files", "get_outline", "get_function_code"], # 侦察兵只能读
    "coder": [], # 纯写代码，不直接调工具
    "writer": ["apply_file_edits", "batch_write_files", "replace_in_file"], # 打字员只能写
    "checker": ["run_terminal_command"], # 测试员只能跑命令
    "rp_character": ["list_lore", "get_prompt"], # RP 角色只能读设定
    "gm": ["add_lore", "update_lore", "delete_lore"], # GM 可以改设定
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
