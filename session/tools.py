# -*- coding: utf-8 -*-
"""会话级工具授权：按会话窗口逐个开关 outreach/web/coding。"""
import json

from session.session import get_session


# ====== 会话级工具授权（按会话窗口逐个授权，而非全局总开关）======
SESSION_TOOL_KEYS = ("outreach", "web", "coding")
# 默认值与前端开关一致：主动联系默认开，联网检索 / 本地项目操控默认关。
SESSION_TOOL_DEFAULTS = {"outreach": True, "web": False, "coding": False}


def get_session_tools(session_id):
    """读取本会话的工具授权状态；文件缺失或损坏时回落到默认。"""
    cfg = dict(SESSION_TOOL_DEFAULTS)
    try:
        f = get_session(session_id).dir / "tools.json"
        if f.exists():
            saved = json.loads(f.read_text(encoding="utf-8"))
            for k in SESSION_TOOL_KEYS:
                if k in saved:
                    cfg[k] = bool(saved[k])
    except Exception:
        pass
    return cfg


def set_session_tools(session_id, patch):
    """按会话窗口合并写入工具授权，返回写入后的完整状态。"""
    cfg = get_session_tools(session_id)
    for k in SESSION_TOOL_KEYS:
        if isinstance(patch, dict) and k in patch:
            cfg[k] = bool(patch[k])
    f = get_session(session_id).dir / "tools.json"
    f.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg
