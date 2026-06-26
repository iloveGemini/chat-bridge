# -*- coding: utf-8 -*-
"""提示词动态拼装引擎：读取提示词文件 / 解析预设 / 宏替换 / 拼 header 与 tail。"""
import json

from core.paths import PROMPTS_DIR, PRESETS_DIR
from core.net import log_print, _safe_name


def _read_prompt_content(category, name):
    """按 分类+名字 读取提示词正文。兜底链：用户库 → data/prompts/_defaults/<cat>/。"""
    if not name:
        return ""
    safe = _safe_name(name)
    for base in (PROMPTS_DIR, PROMPTS_DIR / "_defaults"):
        p_file = base / category / f"{safe}.json"
        if p_file.exists():
            try:
                return (
                    json.loads(p_file.read_text(encoding="utf-8"))
                    .get("content", "")
                    .strip()
                )
            except Exception as e:
                log_print(f"[警告] 读取提示词文件 {p_file} 失败，尝试回落默认: {e}")
                continue
    return ""


def _resolve_preset(preset_name):
    """预设是若干文本提示词分类的引用包。返回 {cat: name} dict（缺失回 default）。"""
    refs = {c: "default" for c in PRESET_CATEGORIES}
    if not preset_name:
        return refs
    p_file = PRESETS_DIR / f"{_safe_name(preset_name)}.json"
    if p_file.exists():
        try:
            d = json.loads(p_file.read_text(encoding="utf-8"))
            for c in PRESET_CATEGORIES:
                if d.get(c):
                    refs[c] = d.get(c)
        except Exception as e:
            log_print(f"[警告] 读取预设 {p_file} 失败: {e}")
    return refs


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


# build_header_prompt / build_tail_anchor 已并入 prompts.assembler.PromptAssembler（槽位骨架）


# 提示词大类常量（从 server.py 迁来，供路由层与初始化共用）
PRESET_CATEGORIES = ["main", "world", "style", "post", "reasoning"]
PROMPT_CATEGORIES = ["main", "character", "user", "world", "style", "post", "reasoning"]
