# -*- coding: utf-8 -*-
"""Output_Format —— 输出格式「条目开关集」（ARCHITECTURE.md §1 的轻量版）。

模型：
  - 内置目录 BUILTIN_FORMATS（写在代码里，用户删不掉，是功能兜底）。
  - 用户可追加自定义条目，存 data/prompts/output_format/<key>.json。
  - 预设存「启用了哪些条目」（preset.json 的 output_format=[keys...]）。
  - 会话可整套覆盖预设（data/sessions/<id>/output_format.json: {set:bool, enabled:[...]}）。
    set 为真时用会话这一整套、忽略预设；否则回落预设；预设没配则用内置 default_on。

每条 = {key, label, desc, tag, fragment, default_on, custom?}
  fragment 是注入提示词的格式规范片段；tag 仅作展示/未来解析器用。
默认所有内置条目 default_on=False → 默认不注入任何东西，行为与改造前等价。
"""
import json

from core.paths import PROMPTS_DIR, PRESETS_DIR
from core.net import log_print, _safe_name

OF_DIR = PROMPTS_DIR / "output_format"

# ---------------- 内置目录（代码兜底，不可删） ----------------
BUILTIN_FORMATS = [
    {
        "key": "scene_transition",
        "label": "场景转场",
        "desc": "发生明显时间流逝或地点转移时，输出一行场景标签。",
        "tag": "scene",
        "default_on": False,
        "fragment": (
            "当发生重大时间流逝或地点转移时（如吃完饭回教室、第二天清晨、去了别处），"
            "在正文前加一行自闭合场景标签：<scene time=\"Day 2·清晨\" place=\"教学楼走廊\" />，"
            "随后接正文。时间地点没大变化就不要输出该标签。"
        ),
    },
    {
        "key": "hidden_thinking",
        "label": "隐藏思考链",
        "desc": "把推理过程包进 <thinking>，前端折叠为思考面板、不污染正文。",
        "tag": "thinking",
        "default_on": False,
        "fragment": (
            "如需展开推理，请把思考过程放进 <thinking>…</thinking>，再在其后给出正式回复正文。"
            "<thinking> 内容仅供整理思路，不要在正文里复述。"
        ),
    },
    {
        "key": "emotion",
        "label": "情绪标注",
        "desc": "为本轮回复标注情绪（happy/sad/angry/…），供语音合成表现力。",
        "tag": "emotion",
        "default_on": False,
        "fragment": (
            "在正文外层标注本轮情绪：从 happy/sad/angry/fearful/surprised/disgusted/neutral "
            "中选最贴切的一个，拿不准用 neutral。"
        ),
    },
    {
        "key": "pause_marks",
        "label": "停顿标记",
        "desc": "在需要断句/换气处插入 <#秒数#>，仅服务语音、聊天显示自动隐藏。",
        "tag": "pause",
        "default_on": False,
        "fragment": (
            "在需要断句/迟疑/换气处插入停顿标记 <#0.4#>（秒数 0.1~1.5），一条最多三五处，别滥用。"
            "该标记只服务语音合成。"
        ),
    },
    {
        "key": "multi_paragraph",
        "label": "多气泡分段",
        "desc": "用连续空行把回复拆成多段，前端渲染为多个气泡。",
        "tag": "para",
        "default_on": False,
        "fragment": (
            "把较长回复用空行拆成自然的多个小段（每段独立成一条消息气泡），"
            "像真人连发几条短消息那样，不要堆成一大坨。含代码块时不要拆。"
        ),
    },
]
_BUILTIN_BY_KEY = {f["key"]: f for f in BUILTIN_FORMATS}


# ---------------- 用户自定义条目读写 ----------------
def _list_custom():
    out = []
    if OF_DIR.exists():
        for f in sorted(OF_DIR.glob("*.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                d["key"] = f.stem
                d["custom"] = True
                d.setdefault("label", f.stem)
                d.setdefault("desc", "")
                d.setdefault("tag", "")
                d.setdefault("fragment", "")
                d.setdefault("default_on", False)
                out.append(d)
            except Exception as e:
                log_print(f"[警告] 读取自定义输出格式 {f} 失败: {e}")
    return out


def list_formats():
    """合并内置 + 用户自定义，内置在前。"""
    custom = _list_custom()
    builtin = [{**f, "custom": False} for f in BUILTIN_FORMATS]
    return builtin + custom


def save_custom_format(key, label, desc, fragment, tag=""):
    key = _safe_name(key)
    if not key:
        return False
    if key in _BUILTIN_BY_KEY:
        return False  # 内置 key 不可被自定义覆盖/同名
    OF_DIR.mkdir(parents=True, exist_ok=True)
    (OF_DIR / f"{key}.json").write_text(
        json.dumps(
            {"label": label or key, "desc": desc or "", "tag": tag or "",
             "fragment": fragment or "", "default_on": False},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    return True


def delete_custom_format(key):
    key = _safe_name(key)
    f = OF_DIR / f"{key}.json"
    if key in _BUILTIN_BY_KEY:
        return False  # 内置不可删
    if f.exists():
        f.unlink()
        return True
    return False


def _fragment_for(key):
    if key in _BUILTIN_BY_KEY:
        return _BUILTIN_BY_KEY[key].get("fragment", "")
    f = OF_DIR / f"{_safe_name(key)}.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8")).get("fragment", "")
        except Exception:
            return ""
    return ""


# ---------------- 启用集解析（会话整套覆盖预设） ----------------
def _builtin_default_enabled():
    return [f["key"] for f in BUILTIN_FORMATS if f.get("default_on")]


def get_preset_enabled(preset_name):
    """读预设里启用的 output_format 条目列表；未配置返回 None（交由调用方回落内置默认）。"""
    if not preset_name:
        return None
    p = PRESETS_DIR / f"{_safe_name(preset_name)}.json"
    if p.exists():
        try:
            v = json.loads(p.read_text(encoding="utf-8")).get("output_format")
            if isinstance(v, list):
                return v
        except Exception:
            pass
    return None


def get_session_output_format(session_dir):
    """读会话级覆盖：{set:bool, enabled:[...]}；无文件返回 {set:False, enabled:[]}。"""
    f = session_dir / "output_format.json"
    if f.exists():
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            return {"set": bool(d.get("set")), "enabled": list(d.get("enabled") or [])}
        except Exception:
            pass
    return {"set": False, "enabled": []}


def set_session_output_format(session_dir, set_flag, enabled):
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "output_format.json").write_text(
        json.dumps({"set": bool(set_flag), "enabled": list(enabled or [])},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"set": bool(set_flag), "enabled": list(enabled or [])}


def resolve_enabled(session, preset_name):
    """覆盖链：会话整套覆盖预设 → 预设 → 内置 default_on。"""
    sess = get_session_output_format(session.dir)
    if sess["set"]:
        return sess["enabled"]
    pre = get_preset_enabled(preset_name)
    if pre is not None:
        return pre
    return _builtin_default_enabled()


def build_output_format_block(session, preset_name):
    """拼出 Output_Format 槽正文：启用条目的 fragment 依目录顺序拼接。"""
    enabled = set(resolve_enabled(session, preset_name))
    if not enabled:
        return ""
    order = [f["key"] for f in BUILTIN_FORMATS] + [c["key"] for c in _list_custom()]
    frags = []
    for k in order:
        if k in enabled:
            fr = _fragment_for(k)
            if fr:
                frags.append(f"- {fr}")
    return "\n".join(frags)
