## 目标
重构 Code Agent 为 5 阶段状态机（Plan, Search, Code, Write, Check），并升级为基于 DDD 的多模态生态架构，同时支持前端 UI 进度展示与全局需求确认。

## 计划与执行
- [x] Step 1: 架构目录调整与工具池（Tools）拆分
  - 目的：实现工具复用与 RBAC 权限精准控制。
  - 动作：建立 `tools/registry.py`，拆分工具到 `tools/common/`（如 `ask_user_clarification`）、`tools/coding/`、`tools/rp/`。
- [x] Step 2: 状态机核心与前端进度条（UI）
  - 目的：用 `state.json` 替代 `work_log.md` 驱动前端进度，实现 `update_plan` 工具。
  - 动作：编写 `agents/coding/state.py` 和 `agents/coding/orchestrator.py` 骨架；修改 `frontend/js/views/codeAgentView.js` 顶部显示 Checkbox 进度条。
- [x] Step 3: 角色逻辑（Roles）拆分与 5 阶段实现
  - 目的：将 `agent.py` 的大循环拆解为 5 个独立的 Role (Planner, Searcher, Coder, Writer, Checker)。
  - 动作：在 `agents/coding/roles/` 下实现各角色的专属 Prompt 和工具权限绑定。
- [x] Step 4: 全局需求确认机制
  - 目的：允许 Agent 在任何阶段（不仅是第一轮）推送需求确认卡。
  - 动作：将 `ask_user_clarification` 注册为通用工具，并在 Planner 等角色中开放使用。
- [ ] Step 5: 后端网关对接与测试验证
  - 目的：使 `server.py` 能够正确路由到新的 Coding Orchestrator。
  - 动作：修改路由逻辑，跑通 E2E 测试闭环。

## 状态
- [ ] 运行中
- [ ] Step 3.5: 提示词解耦，将 agents/coding/roles/ 下硬编码的 SYSTEM_PROMPT 抽离到 agents/coding/prompts/ 目录下的独立文件中。
- [ ] Step 4: 后端对接，修改 server.py 作为网关路由，调用新的 Coding Orchestrator
## 状态
- [ ] 运行中
- [x] Step 3.5: 提示词解耦，将 agents/coding/roles/ 下硬编码的 SYSTEM_PROMPT 抽离到 agents/coding/prompts/ 目录下的独立文件中。
- [ ] Step 3.6: 升级 `ask_user_clarification` 工具，支持选项列表（options）和推荐项（recommended），并支持前端渲染需求确认卡。
- [ ] Step 3.7: 完善前端 UI 进度展示，基于 `state.json` 渲染顶部 Checkbox 任务进度，逐步替代 `work_log.md`。
- [ ] Step 4: 后端对接，修改 server.py 作为网关路由，调用新的 Coding Orchestrator

## 进度更新 (本轮)
- [x] Step 2/3 完成度补齐：`agents/coding/orchestrator.py` 的 `run_turn` 从骨架升级为完整的 5 阶段状态机
  （Plan→Search→Code→Write→Check），各阶段按角色加载 prompt + RBAC 工具，复用 agent.py 的
  `_chat`/沙箱/落库/取消/队列；Checker 通过 `[CHECK_PASS]/[CHECK_FAIL]` 决定完成或回喂 Planner（最多 3 轮大循环）。
- [x] Step 4 后端网关：`server.py` `/api/agent/send` 增加路由——默认走新 Orchestrator
  (`agents.coding.orchestrator.run_coding_task`)，`config.coding.orchestrator=false` 可回退旧 `agent.run_agent_turn`。
- [x] `CodingState` 扩展 set/get/update 与阶段交接字段 (plan_text/search_text/diffs_text/last_error/cycle/status)。
- [x] 修复 config.json 末尾 4 个 NUL 字节导致的 JSON 解析失败（上轮窗口崩溃残留；设置项完好，仅去除垃圾字节）。
- [x] 修复 server.py 末尾被截断（上轮残留）：从 git HEAD 恢复调度日志之后到 server.shutdown() 的完整收尾。
- 验证：state/orchestrator/server/agent 全部 py_compile 通过；离线 mock 烟测 3 项通过
  （happy 直通 done / check 失败回环后通过 / Planner 推确认卡暂停 waiting_user）。
- [ ] Step 3.6/3.7 前端：确认卡渲染 + 顶部 state.json 进度条（本轮未做，按计划下轮）。
- [ ] Step 5 E2E：真实 LLM 跑通一次完整闭环（本轮未做）。

## 模块化拆分 (server 瘦身, 第 1 刀)
- 目标：把 server.py 从单体逐步拆成「只剩 HTTP 层」，共享状态集中到 core/ 单例层。
- [x] 新建 core/ 包：paths.py(路径+端口常量) / net.py(log_print, _http_post_json, _safe_decode) /
      config.py(config 单例 + config_lock + load_config/save_config + 鉴权助手)。
- [x] 关键修复：load_config 改为【就地 clear()+update()】刷新 config，杜绝「重新赋值导致
      各模块 import 到旧引用」的坑。集成测试已验证 server.config is core.config 恒成立。
- [x] server.py 改为从 core 导入这些符号（保留裸名引用与 server.xxx 向后兼容），删除原定义。
- [x] 发现并修复：本机文件系统的写入【不截断旧内容】，缩短文件后尾部残留 NUL 字节
      （config.json 之前那 4 个、server.py 这次 4691 个都是同一成因）。已 strip 干净。
- 验证：core 与 server 均 py_compile 通过；子进程 import server 成功，config 单例跨模块同一、
      default_user=Joy 保留、鉴权/路径/日志全部走 core。
- 下一刀（未做）：session（ChatSession+get_session+sessions_map）、prompts、memory 依次外迁，
      最后把 79 个路由按域拆进 routes/。

## 模块化拆分 (server 瘦身, 第 2 刀: session 域)
- [x] 新建 session/ 包：session.py 收纳 ChatSession 类、get_session、sessions_map、
      global_pending_event、_session_scope、_resolve_session_worldbooks，以及 GENESIS_SCENE /
      SESSION_BINDING_KEYS 两个会话域常量。
- [x] sessions_map / global_pending_event 同样是【就地修改】单例，server 改为 import 回来，
      集成测试已验证 server.sessions_map is session.session.sessions_map 恒成立。
- [x] 依赖干净：session.py 只依赖 core.paths 与 memory_store（已确认 memory_store 无回环）。
- 验证：py_compile 通过；import server 成功，ChatSession/get_session/单例身份全部指向 session 模块，
      config 仍跨模块同一；实际 get_session() 建会话、场景闩锁、绑定键均正常；记忆区头注释无重复。
