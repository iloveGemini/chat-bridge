# -*- coding: utf-8 -*-
"""四层消息协议（角色间交接信封）：Task / Report / Decision / Block。

统一 meta 头 + confidence + block_reason。轻量约定：builder 生成、落 devteam_messages 表，
作为 orchestrator 的交接体；validate 只 warn 不硬拒。confidence 分档不死锁：
  <=30 → block（必带 block_reason，转 Manager 复核）
  31~70 → warn（继续但标记）
  >=71 → continue
"""
import uuid
from dataclasses import dataclass, field

import runtime.devteam_store as store

TYPES = ("TASK", "REPORT", "DECISION", "BLOCK")
BLOCK_THRESHOLD = 30
WARN_THRESHOLD = 70


@dataclass
class Message:
    type: str                       # TASK | REPORT | DECISION | BLOCK
    task_id: str = ""
    from_: str = ""
    to: str = ""
    priority: str = "normal"        # low | normal | high
    confidence: int = 100           # 0~100
    block_reason: str = ""
    payload: dict = field(default_factory=dict)   # 各层特有字段
    message_id: str = field(default_factory=lambda: "m_" + uuid.uuid4().hex[:10])


def _msg(mtype, task_id, from_, to, payload, *, confidence=100, priority="normal",
         block_reason=""):
    return Message(type=mtype, task_id=task_id, from_=from_, to=to,
                   priority=priority, confidence=int(confidence),
                   block_reason=block_reason, payload=payload or {})


def build_task(task_id, from_, to, *, goal, input=None, constraints=None,
               output=None, confidence=100, priority="normal"):
    return _msg("TASK", task_id, from_, to, {
        "goal": goal, "input": input or [], "constraints": constraints or [],
        "expected_output": output or [],
    }, confidence=confidence, priority=priority)


def build_report(task_id, from_, to, *, result, changed=None, issues=None,
                 confidence=100, priority="normal"):
    return _msg("REPORT", task_id, from_, to, {
        "result": result, "changed": changed or [], "issues": issues or [],
    }, confidence=confidence, priority=priority)


def build_decision(task_id, from_, to, *, choice, reason, impact="",
                   confidence=100, priority="normal"):
    return _msg("DECISION", task_id, from_, to, {
        "choice": choice, "reason": reason, "impact": impact,
    }, confidence=confidence, priority=priority)


def build_block(task_id, from_, to, *, missing, why, need, confidence=0,
                priority="high"):
    return _msg("BLOCK", task_id, from_, to, {
        "missing": missing, "why": why, "need": need,
    }, confidence=confidence, priority=priority, block_reason=why)


def confidence_band(confidence):
    """分档（不死锁）：block / warn / continue。"""
    c = int(confidence if confidence is not None else 100)
    if c <= BLOCK_THRESHOLD:
        return "block"
    if c <= WARN_THRESHOLD:
        return "warn"
    return "continue"


# 各类型必备字段（缺了只 warn，不硬拒）
_REQUIRED = {
    "TASK": ["goal"],
    "REPORT": ["result"],
    "DECISION": ["choice", "reason"],
    "BLOCK": ["missing", "why", "need"],
}


def validate(msg: Message):
    """软校验：返回 warning 列表（空=通过）。"""
    warns = []
    if msg.type not in TYPES:
        warns.append(f"未知消息类型: {msg.type}")
    for f in _REQUIRED.get(msg.type, []):
        if not (msg.payload or {}).get(f):
            warns.append(f"{msg.type} 缺字段: {f}")
    if confidence_band(msg.confidence) == "block" and not msg.block_reason:
        warns.append("confidence 过低但未给 block_reason")
    return warns


def persist(msg: Message):
    """落 devteam_messages 表，返回行 id。"""
    return store.append_message(
        msg.task_id, msg.type, from_role=msg.from_, to_role=msg.to,
        priority=msg.priority, confidence=msg.confidence,
        block_reason=msg.block_reason, payload=msg.payload,
    )
