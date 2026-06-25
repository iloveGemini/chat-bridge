# -*- coding: utf-8 -*-
"""场景闩锁：当前时空坐标的盖戳与注入块。"""
from session.session import GENESIS_SCENE


def _scene_stamp(session):
    """当前时空坐标，盖在每条 message 上（落库随消息走）。"""
    return {
        "scene_id": session.current_scene_id,
        "time": session.current_time,
        "place": session.current_place,
    }


def build_scene_block(session):
    """api 模式：拼给 Chat AI 的【当前场景状态 + 信封/转场规约】，注入 tail_anchor。"""
    cur_id = getattr(session, "current_scene_id", GENESIS_SCENE["scene_id"])
    cur_t = getattr(session, "current_time", GENESIS_SCENE["time"])
    cur_p = getattr(session, "current_place", GENESIS_SCENE["place"])
    block = (
        "<current_scene_state>\n"
        f"当前场景ID：{cur_id}\n"
        f"当前剧情时间：{cur_t}\n"
        f"当前所处地点：{cur_p}\n"
        "</current_scene_state>\n"
    )
    return block
