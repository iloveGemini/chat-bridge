# -*- coding: utf-8 -*-
"""Agent 提示词「预设」管理。

每个 agent（按其 prompt_id，如 coding 的 planner/searcher/...）可以有：
  - 一个内置「默认」预设 = 随程序发布的 .md 原文（删不掉，是兜底）。
  - 若干用户自定义预设。
  - 一个「当前启用」的选择（active）；未选时回落默认。

存储：data/prompts/agent/<prompt_id>.json
  { "active": <preset_name|null>, "presets": { <name>: <content>, ... } }
active 为空/None 或指向不存在的预设 → 用默认（.md）。

设计上与具体 agent 解耦：谁有 prompt_id 谁就能被管理，新增 agent 无需改这里。
"""
import json

from core.paths import PROMPTS_DIR
from core.net import _safe_name

AGENT_DIR = PROMPTS_DIR / "agent"
DEFAULT_KEY = "__default__"      # 代表内置 .md 默认预设
DEFAULT_LABEL = "默认（随程序发布）"


def _path(agent):
    return AGENT_DIR / f"{_safe_name(agent)}.json"


def _load(agent):
    p = _path(agent)
    if p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                d.setdefault("active", None)
                d.setdefault("presets", {})
                if not isinstance(d["presets"], dict):
                    d["presets"] = {}
                return d
        except Exception:
            pass
    return {"active": None, "presets": {}}


def _save(agent, data):
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    _path(agent).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def effective_prompt(agent, default_text):
    """返回该 agent 当前生效的系统提示词正文：选中的自定义预设，否则默认 .md。"""
    d = _load(agent)
    active = d.get("active")
    if active and active != DEFAULT_KEY:
        content = d.get("presets", {}).get(active)
        if content is not None and str(content).strip():
            return content
    return default_text


def list_presets(agent, default_text=""):
    """列出该 agent 的全部预设（含内置默认）及当前启用项。"""
    d = _load(agent)
    presets = d.get("presets", {})
    active = d.get("active") or DEFAULT_KEY
    if active != DEFAULT_KEY and active not in presets:
        active = DEFAULT_KEY  # 选中的自定义被删了 → 回落默认
    items = [{"key": DEFAULT_KEY, "label": DEFAULT_LABEL, "builtin": True}]
    for name in presets:
        items.append({"key": name, "label": name, "builtin": False})
    return {"agent": agent, "active": active, "presets": items}


def get_content(agent, key, default_text=""):
    """取某个预设的正文。内置默认返回 .md 原文。"""
    if not key or key == DEFAULT_KEY:
        return default_text
    return _load(agent).get("presets", {}).get(key, "")


def save_preset(agent, name, content):
    """新增/覆盖一个自定义预设。name 不能是保留的默认 key。"""
    name = (name or "").strip()
    if not name or name == DEFAULT_KEY:
        return False, "预设名非法"
    d = _load(agent)
    d["presets"][name] = content or ""
    _save(agent, d)
    return True, None


def delete_preset(agent, name):
    """删除一个自定义预设；若它正被启用，则回落默认。"""
    d = _load(agent)
    if name in d.get("presets", {}):
        del d["presets"][name]
        if d.get("active") == name:
            d["active"] = None
        _save(agent, d)
        return True
    return False


def select_preset(agent, key):
    """设置当前启用的预设。key=__default__ 或空 → 启用内置默认。"""
    d = _load(agent)
    if not key or key == DEFAULT_KEY:
        d["active"] = None
    else:
        if key not in d.get("presets", {}):
            return False
        d["active"] = key
    _save(agent, d)
    return True
