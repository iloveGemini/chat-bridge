# -*- coding: utf-8 -*-
"""<msg> 信封解析与回复摄取：抽场景元数据/正文/情绪/停顿标记，推进场景闩锁。"""
import re

from core.net import log_print


def parse_msg_envelope(raw):
    """解析 <msg> 信封。返回 (clean_content:str, scene:dict|None, meta:dict)。
    scene 含 scene_id/time/place（缺项为 None）。
    meta = {emotion, voice_text}：emotion 取自 <content emotion="...">；
    voice_text 保留 <#x#> 停顿标记供 TTS，clean_content 抹掉这些标记供展示。
    解析失败时正文回退原文。"""
    if not raw:
        return "", None, {}
    text = raw.strip()
    scene = None
    meta = {}

    # 1. 抽场景元数据（自闭合 <scene .../>，属性顺序无关，容忍多空格/无斜杠）
    m = re.search(r"<scene\b([^>]*?)/?>", text, re.I)
    if m:
        attrs = m.group(1)

        def _attr(name):
            am = re.search(name + r'\s*=\s*"([^"]*)"', attrs, re.I)
            return am.group(1).strip() if am else None

        sid, t, p = _attr("id"), _attr("time"), _attr("place")
        if sid or t or p:
            scene = {"scene_id": sid, "time": t, "place": p}
        text = (text[: m.start()] + text[m.end() :]).strip()  # 物理超度 scene 标签

    # 2. 抽正文：优先 <content emotion="...">…</content>
    cm = re.search(r"<content\b([^>]*)>(.*?)</content>", text, re.I | re.S)
    if cm:
        em = re.search(r'emotion\s*=\s*"([^"]*)"', cm.group(1), re.I)
        if em and em.group(1).strip():
            meta["emotion"] = em.group(1).strip()
        inner = cm.group(2).strip()
    else:
        # 兜底：剥掉残留的 <msg>/<content> 标签，剩下的整段当正文
        inner = re.sub(r"</?(?:msg|content)\b[^>]*>", "", text, flags=re.I).strip()

    # 3. 停顿标记 <#x#>：保留给语音，抹掉给展示
    display = re.sub(r"<#[^>]*?#>", "", inner)
    display = re.sub(r"[ \t]{2,}", " ", display).strip()
    if inner != display:
        meta["voice_text"] = inner
    return display, scene, meta


def ingest_reply(session, raw_reply):
    """解析 AI 原始输出，推进场景闩锁，返回 (干净正文, meta)。两模式共用。
    meta 含可选 emotion / voice_text，供落库后给 TTS 用。"""
    content, scene, meta = parse_msg_envelope(raw_reply)
    if scene:
        with session.lock:
            if scene.get("scene_id"):
                session.current_scene_id = scene["scene_id"]
            if scene.get("time"):
                session.current_time = scene["time"]
            if scene.get("place"):
                session.current_place = scene["place"]
        log_print(
            f"🎬 [场景转换] -> {session.current_scene_id} ({session.current_time} @ {session.current_place})"
        )
    # 兜底：正文为空（极端：只发了 scene 标签）时退回原文，绝不给前端空消息
    display = content if content else (raw_reply or "").strip()
    return display, meta
