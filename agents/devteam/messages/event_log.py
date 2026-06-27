# -*- coding: utf-8 -*-
"""事件日志 —— 与 Message、State 分离的第三种记录。

事件 = 状态/生命周期发生了什么（谁、改了什么、为什么）。STATE_CHANGED 由 ProjectStateStore
负责写；这里集中其它生命周期事件（派单 / 确认门 / 路由）的便捷追加，统一进 devteam_events。
"""
import runtime.devteam_store as store

# 事件类型常量
STATE_CHANGED = "STATE_CHANGED"     # 由 store 写：状态树变更（带 before/after）
PHASE_CHANGED = "PHASE_CHANGED"     # 阶段推进（语义事件，可与 STATE_CHANGED 并存）
ROLE_DISPATCHED = "ROLE_DISPATCHED"  # Manager 派单给某角色
GATE_RAISED = "GATE_RAISED"          # 升起一个待用户确认的检查点门
GATE_APPROVED = "GATE_APPROVED"      # 用户批准了某检查点门
ROUTE = "ROUTE"                      # 路由决策（auto / 手动 @角色）
WARN = "WARN"                        # 软校验告警


def log(task_id, event_type, actor="", reason="", before=None, after=None):
    return store.append_event(task_id, event_type, actor=actor,
                              before=before, after=after, reason=reason)


def list_events(task_id, after_id=0):
    return store.list_events(task_id, after_id)
