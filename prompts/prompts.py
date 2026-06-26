# -*- coding: utf-8 -*-
"""提示词动态拼装引擎：读取提示词文件 / 解析预设 / 宏替换 / 拼 header 与 tail。"""
import json

from core.paths import PROMPTS_DIR, PRESETS_DIR
from core.net import log_print, _safe_name
from core.config import config, load_config as _load_config
from chat.scene import build_scene_block


def _read_prompt_content(category, name):
    """按 分类+名字 读取某个提示词文件的正文内容；读不到返回空串。"""
    if not name:
        return ""
    p_file = PROMPTS_DIR / category / f"{_safe_name(name)}.json"
    if p_file.exists():
        try:
            return (
                json.loads(p_file.read_text(encoding="utf-8"))
                .get("content", "")
                .strip()
            )
        except Exception as e:
            log_print(f"[警告] 读取提示词文件 {p_file} 失败: {e}")
    return ""


def _resolve_preset(preset_name):
    """预设是 {main,style,post} 的引用包。返回 (main_name, style_name, post_name)。"""
    if not preset_name:
        return ("default", "default", "default")
    p_file = PRESETS_DIR / f"{_safe_name(preset_name)}.json"
    if p_file.exists():
        try:
            d = json.loads(p_file.read_text(encoding="utf-8"))
            return (
                d.get("main", "default"),
                d.get("style", "default"),
                d.get("post", "default"),
            )
        except Exception as e:
            log_print(f"[警告] 读取预设 {p_file} 失败: {e}")
    return ("default", "default", "default")


def _get_display_name(category, file_name, default_val):
    """读取角色/用户设定的真实展示名称"""
    if not file_name or file_name == "default":
        return default_val
    p_file = PROMPTS_DIR / category / f"{_safe_name(file_name)}.json"
    if p_file.exists():
        try:
            return json.loads(p_file.read_text(encoding="utf-8")).get("name", file_name)
        except Exception as e:
            log_print(f"[警告] 读取展示名称 {p_file} 失败: {e}")
    return file_name


def _apply_macros(text, char_name, user_name):
    """替换全局宏变量 {{char}} 和 {{user}}"""
    if not text:
        return text
    return text.replace("{{char}}", char_name).replace("{{user}}", user_name)


def build_header_prompt(session):
    """
    【顶部磁铁】只放永恒不变的客观背景定义
    顺位：Role (Main/全局兜底) -> World Setting -> Persona -> Target User
    """
    _load_config()
    global_sys = config.get("api", {}).get("system_prompt", "").strip()
    active = session.active_prompts

    if "preset" in active and active.get("preset") not in ("", "default"):
        main_name, _, _ = _resolve_preset(active.get("preset"))
    else:
        main_name = active.get("main", "default")

    main_content = _read_prompt_content("main", main_name)
    if (not main_content or main_name == "default") and global_sys:
        main_content = global_sys

    parts = []
    if main_content:
        parts.append(f"<role_definition>\n{main_content}\n</role_definition>")

    c = _read_prompt_content("character", active.get("character", "default"))
    if c:
        parts.append(f"<persona>\n{c}\n</persona>")

    u = _read_prompt_content("user", active.get("user", "default"))
    if u:
        parts.append(f"<user_profile>\n{u}\n</user_profile>")

    return "\n\n".join(parts)


def build_tail_anchor(session, memory_str=""):
    """
    【尾部磁铁】动态驱动上下文与强制约束（定海神针）
    顺位：[召回的长时记忆 Memory] -> Dialogue Style -> Output Rules (Post)
    """
    active = session.active_prompts
    if "preset" in active and active.get("preset") not in ("", "default"):
        _, style_name, post_name = _resolve_preset(active.get("preset"))
    else:
        style_name = active.get("style", "default")
        post_name = active.get("post", "default")

    parts = []

    # 0. 当前场景状态 + 结构化输出/转场规约（动态，故放尾部而非静态 header）
    parts.append(build_scene_block(session))

    # 1. 召回的动态记忆
    if memory_str.strip():
        parts.append(f"<recalled_memory>\n{memory_str.strip()}\n</recalled_memory>")

    # 2. 说话文风
    s = _read_prompt_content("style", style_name)
    if s:
        parts.append(f"<dialogue_style>\n{s}\n</dialogue_style>")

    # 3. 压轴输出规约 (post)
    p = _read_prompt_content("post", post_name)
    if p:
        parts.append(f"<output_rules>\n{p}\n</output_rules>")

    compiled = "\n\n".join(parts)
    if compiled:
        # 用显式系统控制块包裹，防止 AI 误以为这些指令是普通用户打出来的字
        return f"\n\n\n<system_guidance>\n{compiled}\n</system_guidance>"
    return ""


# 提示词大类常量（从 server.py 迁来，供路由层与初始化共用）
PRESET_CATEGORIES = ["main", "style", "post"]
PROMPT_CATEGORIES = ["main", "character", "user", "style", "post"]
