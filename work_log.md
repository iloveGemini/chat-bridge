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

## 删除「设定助手」功能（按需求移除）
- [x] server.py：删除 BUILTIN_ASSISTANT_PROMPT、ASSISTANT_CHAR_KEY、ASSISTANT_MAX_ROUNDS、
      _list_targets()、call_assistant_api()，以及 call_llm_api 里指向它的派发分支；
      PROTECTED_PROMPT_NAMES 改回 {"default"}。
- [x] tooling.py：删除 get_assistant_tools()、_read_target_session()、execute_tool 内
      「设定助手部分」的全部工具分支（list_targets/list_lore/add_lore/.../bind_preset）；
      PROTECTED_PROMPT_NAMES 改回 {"default"}。Coding 代码工具分支完整保留。
- 验证：server/tooling 均 py_compile 通过；import server 成功，call_llm_api/get_session_tools 健在，
      call_assistant_api/BUILTIN_ASSISTANT_PROMPT/tooling.get_assistant_tools 已不存在，
      tooling 编码工具仍 9 个完整可用；前端无任何 设定助手/__assistant__ 引用。
- 备注（无害遗留，未动）：tools/registry.py 的 get_assistant_tools（新工具系统的通用 legacy 访问器，
      无调用方）、tools/rp/list_targets.py 里对 "__assistant__" 的防御性过滤。两者都不是设定助手功能本体，
      留着零风险；要彻底清可下轮顺手删。

## 模块化拆分 (server 瘦身, 第 3 刀: prompts + scene 域)
- [x] _safe_name 迁入 core.net（通用名字净化，server 内 20 处引用改为导入）。
- [x] 新建 chat/ 包 + chat/scene.py：_scene_stamp、build_scene_block（依赖 session.session 的 GENESIS_SCENE）。
- [x] 新建 prompts/ 包 + prompts/prompts.py：_read_prompt_content、_resolve_preset、_get_display_name、
      _apply_macros、build_header_prompt、build_tail_anchor（依赖 core.paths/net/config + chat.scene）。
- 依赖链无环：prompts → chat.scene → session.session → core；server 仅 import 回这些符号。
- 验证：全部 py_compile 通过；import server 成功，6 个提示词函数 + 2 个场景函数身份均指向新模块；
      实跑 build_header_prompt/build_tail_anchor 拼装正常（含场景块 current_scene_state 与召回记忆注入）。
- 遗留（无害）：测试产生的空目录 prompts_pkg_tmp、__prompttest__ 会话目录沙箱删不掉，data/ 与空目录都不入库。

## 模块化拆分 (server 瘦身, 第 4 刀: memory 域)
- [x] 新建 memory/ 包 + memory/memory.py：嵌入(_embed_cfg/_memory_cfg/embed_texts/embed_query/
      _lore_embedding)、召回注入(build_injected_memory)、增量总结(SUMMARY_SYSTEM_PROMPT/run_summary/
      summarize_session/_summ_*/_needs_summary/_migrate_legacy_memory)，从 server.py 原样抽出。
- [x] _extract_json（memory 与 chat 信封共用的工具）迁入 core.net。
- [x] 大字符串 SUMMARY_SYSTEM_PROMPT 用脚本按源码区间精确搬运，零手抄、零改动。
- 依赖链无环：memory → core + session.session + memory_store(底层库)；server 仅 import 回这些符号。
- 验证：全部 py_compile 通过；import server 成功，记忆各函数身份均指向 memory 模块，_extract_json 指向 core.net；
      端到端实跑（DB 指向 /tmp 绕开挂载盘 sqlite 限制）：build_injected_memory 正常返回、run_summary 完整跑通
      到 API 调用（仅因沙箱无外网 403 失败，逻辑/重试/错误处理均正确）。
- server.py 累计：3754 -> 2900 行级别。已抽出 core / session / chat.scene / prompts / memory 五块。

## 模块化拆分 (server 瘦身, 第 5 刀: chat envelope + tts)
- [x] 新建 chat/envelope.py：parse_msg_envelope（<msg> 信封解析：场景元数据/正文/情绪/停顿标记）、
      ingest_reply（推进场景闩锁）。依赖 re + core.net.log_print。
- [x] 新建 chat/tts.py：_tts_cfg/_strip_narration/synth_tts/_attach_tts/_character_voice（MiniMax T2A 旁路）。
      依赖 core.config/paths/net。
- 验证：全部 py_compile 通过；import server 成功，4 个函数身份均指向 chat.* 新模块；
      实跑信封解析（scene_3/emotion=happy/voice_text 保留 <#0.4#>）、ingest 场景闩锁推进、
      旁白剥离、TTS 未开启返回 None —— 全部正确。
- server.py 累计：3754 -> 2480 行级别。已抽出 core/session/chat(scene+envelope+tts)/prompts/memory。

## 模块化拆分 (server 瘦身, 第 6 刀: proactive + outreach + notify)
- [x] 新建 chat/notify.py：_detect_lan_ip / _resolved_notify_cfg / _push_notify，外加 LAN_BASE 的
      get_lan_base/set_lan_base 访问器（替代原 server 的可变全局，杜绝 from-import 绑定到旧值）。
- [x] 新建 chat/outreach.py：_get_last_user_ts、_generate_proactive_message、_fire_outreach、
      _outreach_enabled、_outreach_tool_defs、_parse_when、_exec_outreach_tool。
- [x] server bootstrap 的 `LAN_BASE = ...` 改为 set_lan_base(...)；删除 server 的 LAN_BASE 全局。
- 依赖链无环：outreach → core + prompts + memory + chat(envelope/scene/notify) + session + scheduler。
- 验证：全部 py_compile 通过；import server 成功，相关函数身份均指向新模块；
      LAN holder set/get 生效、外联工具定义(schedule/list/cancel)、_parse_when(daily/once+2h) 均正确。
- server.py 累计：3754 -> 2200 行级别。

## 模块化拆分 (server 瘦身, 第 7 刀: call_llm_api 主聊天链路)
- [x] 新建 chat/llm.py：call_llm_api（RP 三明治拼装 + 原生 tool_calls 多轮循环 + 信封落库 + 记忆触发）、
      _safe_resolve_path（路径安全卫士）、API_REQUEST_TIMESTAMPS（滑动窗口限流，仅其内部用）。
      顺手删掉死的 `global LAST_API_REQUEST_TIME`。
- [x] 新建 session/tools.py：SESSION_TOOL_KEYS/DEFAULTS + get_session_tools/set_session_tools
      （llm 与 server 路由都要用，放 session 域避免 llm→server 回环）。
- 依赖链无环：llm → core + prompts + memory + chat(envelope/scene/outreach) + session(session/tools) + tooling。
- 验证：全部 py_compile 通过；import server 成功，call_llm_api/get_session_tools/set_session_tools 身份均指向新模块；
      会话工具授权读写闭环正确、_safe_resolve_path 越界拦截、限流表就位。
- server.py 累计：3754 -> 1700 行级别，已基本只剩 HTTP 层（Handler + 路由 + bootstrap）。
