# Chat Bridge 记忆系统 — 项目说明（接手必读）

> 本文档面向"下次接手的人/AI"，帮助快速理解这套统一记忆系统的设计与实现。
> 配套：实现计划见 `C:\Users\布丁\.claude\plans\claude-mode-api-encapsulated-kazoo.md`。

## 1. 这个项目是什么

`C:\Claude Code` 是一个**极简聊天桥接器 / 角色扮演陪伴器**。手机浏览器访问 `http://<本机IP>:8800` 跟 AI 聊天。
有两种回复引擎（`config.json` 的 `mode` 切换）：

- **api 模式**：`server.py` 调外部 LLM 生成回复（当前是本地 gemini 代理 `127.0.0.1:2156`）。
- **claude_mode**：Claude Code 进程通过 `/api/wait_pending` 长轮询接管，由 Claude 本人推理生成回复（见 `CLAUDE.md` 的自动循环）。

## 2. 记忆系统总览（本项目的核心改造）

**目标**：让两种模式**共用同一套记忆**，server 成为记忆的唯一权威。

- 存储：**SQLite `data/memory.db`**（WAL 模式），由 `memory_store.py` 管理。
- 作用域：按**角色**隔离，`scope = "char:<角色名>"`（角色名取自会话的 `active_prompts.character`）。同一角色的多个会话**共享**记忆。
- 写入（总结）和读取（召回）**都由 server 统一负责**，两种模式都不直接碰 DB。
- 向量：线上 **siliconflow `BAAI/bge-m3`（1024 维）**；可选 rerank（默认关，未接入召回路径）。
- MCP（Nocturne）只保留**真实用户本人**的信息，角色/扮演记忆一律进本地库。

## 3. 关键文件

| 文件 | 作用 |
|---|---|
| `server.py` | HTTP 服务 + 双引擎 + 记忆引擎（embed/召回/增量总结/边界推进/`/api/memory/*` 路由） |
| `memory_store.py` | 纯存储层（零网络）：SQLite CRUD、向量编码/cosine、关键词降级检索、`build_memory_context`、meta、编辑函数 |
| `config.json` | 配置：`api`(聊天) / `summary_api`(总结) / `embedding` / `rerank` / `memory` |
| `index.html` | 前端 UI；右侧设置抽屉里有「记忆管理」面板（`openMemoryPanel`） |
| `CLAUDE.md` | claude_mode 的自动启动/消息循环规范（记忆已交给 server，仅真实用户才写 MCP） |

## 4. 数据模型（`memory.db`，5 张表）

- `events`：情节事件。`type`(相遇/冲突/揭示/抉择/羁绊/转变/收束/日常) `weight`(核心/主线/转折/点睛/氛围) `summary`(高召回回忆卡片) `importance`(1-5) `embedding`(BLOB) `scope` `session_id`。
- `chunks`：原文切片（细节召回），带 `embedding`。
- `facts`：SPO 三元组知识图谱，`UNIQUE(scope,subject,predicate)` KV 覆盖；关系类谓词用 `对X的看法`。
- `summaries`：`key` 寻址。`arc:<scope>`=角色关系弧；`session:<id>`=会话滚动近况。
- `meta`：`summ:<session_id>` 存总结边界与状态 `{boundary,state,last_status,last_time,last_error}`。

`memory_store.py` 返回的 dict **不含 embedding 向量**（已剥离，避免塞满 HTTP 响应），算分时从原始行临时解码。

## 5. 配置（config.json）

```jsonc
"api":        { 聊天用 LLM（OpenAI 兼容） },
"summary_api":{ 总结用 LLM；base_url 留空则回退用 api。当前=siliconflow DeepSeek-V3 },
"embedding":  { enabled, base_url, api_key, model=BAAI/bge-m3 },   // 无 key/失败→关键词降级
"rerank":     { enabled:false, ... },                              // 预留，未接入召回
"memory":     { recent_rounds, summarize_every, recall_n, top_k }
```

**单位注意**：`recent_rounds` 单位是**轮**（代码里 ×2 = 条），`summarize_every` 单位是**条**。
例：`recent_rounds=25` → 聊天上下文带最近 50 条原文；`summarize_every=25` → 每攒够 25 条触发一次总结。
不变量：只要 `summarize_every ≤ recent_rounds×2`，消息滑出原文窗口前必已被总结，无记忆缺口。

## 6. 核心流程

### 回复时的上下文组装（前后端分离）
- 前端永远显示全量历史；**后端只打包**「最近 `recent_rounds×2` 条原文 + 记忆块」给 LLM（`call_llm_api`，embedding 在 session 锁外调用）。
- 记忆块 = `build_injected_memory()` → `memory_store.build_memory_context()`：
  1. 恒定注入：facts（SPO）+ 关系弧 + 会话近况
  2. 召回注入：events 向量召回 top_k（weight 加权）+ chunks 向量召回 top_k（细节，去重）
  3. **降级**：embedding 不可用时，1 照常注入，2 退化为「关键词 LIKE + importance + 时间」。

### 增量总结（基于 boundary 推进，server 统一负责）
- `summarize_session(session, full)`：从 `meta.boundary` 起，按 `summarize_every` 条一批，调 `run_summary` 把该批对话蒸馏成 events/facts/arc/session_summary 落库（事件与切片批量算 embedding），然后推进 boundary。
- 触发点：api 模式在 `call_llm_api` 回复后、claude_mode 在 `/api/reply` 后，`_needs_summary()` 判断攒够一批就后台跑一批。
- `full=True`（手动「补总结积压」）会循环把全部积压补完（批间 sleep 2s 避限速）；中途失败可再点，**boundary 已持久化**会从断点续。
- 总结提示词 `SUMMARY_SYSTEM_PROMPT`：参考 SillyTavern/LittleWhiteBox 的"高召回回忆卡片"思路（保留原词、写具体、正反例、SPO 规范）。

## 7. HTTP 接口（记忆相关）

- `GET  /api/memory/overview?session_id=` — 面板一次拉全（计数/arc/近况/events/facts/范围/状态）
- `GET  /api/memory/context?session_id=&q=` — 组装好的记忆块文本（两模式共用）
- `GET  /api/memory/search?session_id=&q=&k=` — 原始召回结果（events+chunks）
- `GET  /api/memory/list?session_id=&kind=` — 列出某类
- `POST /api/memory/summarize` — 触发补总结积压（full）
- `POST /api/memory/edit` — 编辑 events/facts/summaries（改 event.summary 会重算向量）
- `POST /api/memory/forget` — 删除一条

## 8. 前端记忆面板

右侧设置抽屉 →「记忆管理」(`openMemoryPanel` in index.html)：状态栏(已总结 X/Y、计数、成功/失败+原因)、补总结积压按钮(带进度轮询)、关系弧/近况可编辑、硬事实(SPO 可改可删)、事件(摘要可改可删)。

## 9. 已知限制 / 坑

- **siliconflow 免费档每分钟限速低**：快速连发会 429。正常总结频率很低没事；补总结长积压会分批+延时，失败可续。
- **rerank 未接入**：config 有位、默认关；要启用需在召回路径里加重排阶段（参考插件 `reranker.js`）。
- **老会话**（记忆系统上线前的）DB 里无记忆：需手动「补总结积压」补一次。
- **caused_by（因果链）**字段表里有，但当前总结未填充（增量调用没暴露事件 id）。
- 旧的 `data/sessions/*/memory.json` 已不再读取（启动迁移成 arc）；保留无害。

## 10. 怎么测试

- 模块自测：`python memory_store.py`（应打印 ALL TESTS PASSED）。
- 端到端：起 server → 造一个带对话的 session → `POST /api/memory/summarize` → `GET /api/memory/overview` 看抽取 → `GET /api/memory/context?q=...` 看召回。
- 注意每次测完清 `data/memory.db` 和临时 session，避免污染。
- 杀残留 server：`Get-CimInstance Win32_Process -Filter "Name='python.exe'" | ? { $_.CommandLine -like '*server.py*' } | % { Stop-Process -Id $_.ProcessId -Force }`
