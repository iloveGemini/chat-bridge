# -*- coding: utf-8 -*-
"""ProjectStateStore —— 状态树的【唯一写入口】。

角色不得直接给状态赋值；一切变更经此 apply(actor, op)：
  ① 校验（转移合法 / reason 必填 / 角色写层权限软校验）
  ② 算 before/after（完整快照）
  ③ 写一条 STATE_CHANGED 事件（actor/before/after/reason）—— 事件是唯一真相来源
  ④ 刷新物化快照
天然支持 replay（折叠事件）/ rollback / diff。
"""
import copy

import runtime.devteam_store as store
from agents.devteam.state.project_state import default_state, LAYERS
from agents.devteam.state.state_machine import can_transition, role_may_write


def _deep_merge(base, patch):
    """把 patch 深合并进 base（dict 递归合并，其余直接覆盖）。返回新对象，不改入参。"""
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def classify_checkpoint(op):
    """把一个 op 归类为「需要用户确认的检查点」类型；非检查点返回 None。
    只拦三类：phase_change / architecture.approved / release(由 phase_change 覆盖)。"""
    if not op:
        return None
    if op.get("kind") == "phase":
        return "phase_change"
    if op.get("kind") == "layer" and op.get("layer") == "architecture":
        if (op.get("patch") or {}).get("approved") is True:
            return "architecture.approved"
    return None


class ProjectStateStore:
    def __init__(self, task_id, name=""):
        self.task_id = task_id
        self.name = name

    # ---------- 读 ----------
    def snapshot(self):
        snap = store.load_snapshot(self.task_id)
        if snap is None:
            snap = default_state(self.task_id, self.name)
        return snap

    # ---------- 写（唯一入口） ----------
    def apply(self, actor, op):
        """op = {kind:'phase', to, reason} | {kind:'layer', layer, patch, reason}。
        返回 {ok, rejected?, warn?, event_id?, before, after, checkpoint}。"""
        reason = (op.get("reason") or "").strip()
        if not reason:
            return self._reject(actor, op, "缺少 reason（每次状态变更必须说明原因）")

        kind = op.get("kind")
        if kind == "phase":
            return self._apply_phase(actor, op.get("to"), reason)
        if kind == "layer":
            return self._apply_layer(actor, op.get("layer"), op.get("patch") or {}, reason)
        return self._reject(actor, op, f"未知 op 类型: {kind}")

    def _apply_phase(self, actor, to_phase, reason):
        before = self.snapshot()
        cur = before["project"]["phase"]
        if not can_transition(cur, to_phase):
            return self._reject(
                actor, {"kind": "phase", "to": to_phase},
                f"非法阶段转移：{cur} → {to_phase}",
            )
        after = _deep_merge(before, {"project": {"phase": to_phase}})
        if to_phase == "release":
            after = _deep_merge(after, {"project": {"status": "completed"}})
        return self._commit(actor, before, after, reason,
                            checkpoint=classify_checkpoint({"kind": "phase", "to": to_phase}))

    def _apply_layer(self, actor, layer, patch, reason):
        before = self.snapshot()
        if layer not in LAYERS:
            return self._reject(actor, {"kind": "layer", "layer": layer},
                                f"未知状态层: {layer}")
        warn = None
        if not role_may_write(actor, layer):
            # 软校验：越权不硬拦，但记 WARN 事件并在结果里标记
            warn = f"角色越权写层：{actor} 不该写 {layer}（已记录，按轻量约定放行）"
            store.append_event(self.task_id, "WARN", actor=actor,
                               before=None, after=None, reason=warn)
        after = _deep_merge(before, {layer: patch})
        res = self._commit(actor, before, after, reason,
                           checkpoint=classify_checkpoint({"kind": "layer", "layer": layer, "patch": patch}))
        if warn:
            res["warn"] = warn
        return res

    def _commit(self, actor, before, after, reason, checkpoint=None):
        eid = store.append_event(self.task_id, "STATE_CHANGED", actor=actor,
                                 before=before, after=after, reason=reason)
        store.save_snapshot(self.task_id, after)
        return {"ok": True, "event_id": eid, "before": before, "after": after,
                "checkpoint": checkpoint}

    def _reject(self, actor, op, why):
        store.append_event(self.task_id, "WARN", actor=actor, before=None, after=None,
                          reason=f"拒绝 {op}: {why}")
        return {"ok": False, "rejected": True, "reason": why}
