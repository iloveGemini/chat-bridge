# 世界书（World Book / Lorebook）实施计划

> 目标：把现在几千 token 的「角色设定 / 世界设定 / 用户设定」从全量常驻，改成 **分层 + 按需召回**。
> 省的是 token，护的是 AI 的注意力。复用现有 `data/memory.db` 与向量管线，**不引入新依赖**。

---

## 0. 现状与动机

- 现在动态记忆（events/facts/chunks/summaries）已经在 SQLite + 向量里按需召回了。
- 但**静态设定**（人设全文、世界观、用户设定）目前是全量塞进 context 的，几千 token 常驻。
- 问题不在 token 成本，而在**注意力稀释**：大量当前场景无关的设定挤占了模型的有效注意力。
- 业界（SillyTavern 等）的解法是 World Info / Lorebook：设定切成条目，命中才注入。

---

## 1. 分层设计

把设定拆成两类：

### Tier 0 — 常驻（压到最小，目标 < 600 token）
- 角色核心身份 / 语气骨架（不含支线细节）
- 当前 scene 状态（`time` / `place`，已有）
- 关系近况一句话摘要

> Tier 0 决定「AI 始终是谁」，必须永远在场。

### Tier 1 — 按需（世界书条目，命中才注入）
- 世界观细节、地点设定、配角档案、历史背景
- 用户设定里的边角信息
- 任何「只在特定场景才相关」的设定

---

## 2. 数据模型

新增一张 `lore` 表，与现有记忆表并列于 `data/memory.db`：

```sql
CREATE TABLE IF NOT EXISTS lore (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scope       TEXT NOT NULL,        -- 'char:<角色名>' / 'world:<世界名>' / 'user'
    title       TEXT NOT NULL,
    keys        TEXT NOT NULL,        -- JSON 数组，触发词+别名 ["教学楼","走廊","三楼"]
    content     TEXT NOT NULL,        -- 这条设定正文
    priority    INTEGER DEFAULT 0,    -- 抢 token 预算时的排序权重
    always_on   INTEGER DEFAULT 0,    -- 1 = Tier 0，永远注入
    embedding   BLOB,                 -- 语义召回向量，复用现有 embed 管线
    created_at  REAL,
    updated_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_lore_scope ON lore(scope);
```

> 隔离规则沿用记忆：按 `scope` 隔离，同角色多会话共享。

---

## 3. 召回逻辑（每轮拼一次）

双通道召回——关键词保证专有名词精准，向量兜底模糊场景。

```
def recall_lore(session_id, current_msg, recent_msgs, scene, budget_tokens):
    # 1) always_on 直接进
    selected = query(scope, always_on=1)

    # 2) 关键词触发
    #    扫描范围 = 当前消息 + 最近 N 条 + scene.place + scene.time
    scan_text = current_msg + join(recent_msgs[-N:]) + scene.place + scene.time
    for entry in keyed_entries(scope):
        if any(key in scan_text for key in entry.keys):
            selected.add(entry)

    # 3) 语义召回
    q_vec = embed(current_msg + recent_summary)
    for entry, score in topk_by_cosine(q_vec, lore_embeddings, threshold):
        selected.add(entry)

    # 4) 合并去重 → 按 priority 降序 → 填到 budget 上限为止
    return pack(dedup(selected), budget_tokens)
```

### 接入点
- **不新开接口**：把结果并入 `/api/memory/context` 返回体的一个 `lore` 字段。
- 或新增 `/api/lore/context?session_id=&q=`，与记忆召回并行。
- 回复循环里：需要时（场景切换 / 用户提到旧设定）拉一次，日常闲聊可跳过保持极速。

---

## 4. 关键工程坑（必须处理）

| 坑 | 现象 | 对策 |
|---|---|---|
| **抖动** | 条目一会儿进一会儿出，AI「忽然忘事」 | 迟滞：命中后保持 warm 数轮再退场（记 last_hit 轮次） |
| **冷启动** | scene 第一句上下文少，召不准 | 多用 `scene.place/time` 当补充查询 |
| **递归触发** | 条目触发条目，雪崩 | 设深度上限（建议 ≤ 2 层） |
| **位置稀释** | 设定埋在 context 最前被稀释 | 召回块尽量靠近生成点（context 末尾） |
| **预算超支** | 命中过多撑爆 | 硬性 token 上限，按 priority 截断 |

---

## 5. 落地步骤（建议顺序）

1. **建表 + 迁移**：在 `server.py` 初始化里加 `lore` 表 DDL（幂等）。
2. **手动灌一批条目**：把现有人设/世界设定拆成 8~15 条，标好 keys / always_on / priority。
3. **写 `recall_lore`**：先只做「关键词 + always_on」两通道，跑通最简版。
4. **接 `/api/memory/context`**：把 `lore` 字段并进返回体，回复循环里用上。
5. **加语义通道**：复用现有 embed，给 lore 补 embedding，加 top-k。
6. **加迟滞 + 预算上限**：解决抖动和超支。
7. **观测**：log 每轮命中了哪些条目、占多少 token，调阈值。

> 与现有 roadmap 对齐：这属于「召回可观测」之后、独立设定治理的一块，可在记忆召回打磨稳定后再上。

---

## 6. 验证

- 准备 3~4 个测试场景（不同 place/time + 提到特定配角/历史），断言：
  - 相关条目被召回、无关条目不被召回
  - Tier 0 永远在场
  - 总注入 token 不超预算
- 对比上线前后：同一段对话，context 里静态设定 token 数应显著下降，角色一致性不退化。
