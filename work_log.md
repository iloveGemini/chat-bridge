## 2024-05-20: 实现了AI思考链与工具调用的前端气泡显示
修改了 server.py 和 chatView.js，将 reasoning 和 tool_call 作为特殊类型消息直接落库并推送到前端，并在前端新增了样式渲染。
## 2024-05-20: 修复了终端执行 echo 追加日志时的乱码问题，修改了 tooling.py。遗留问题：修改需要重启后端才能生效。\n## 2024-05-20: 优化了工具与思考链的前端显示面板，移除了顶部悬浮绿色气泡，并完善了渲染逻辑。 
## 2026-06-24: 优化了思考链和工具调用的前端显示UI。修改了 chatView.js 和 chat.css。 
"## 2024-05-30: 修复了前端思考过程和工具调用的样式，移除了 msg-bubble 类，避免了主题样式的污染。" 
"## 2024-05-30: 修复了工具调用状态一直显示执行中的bug（根据是否为最后一条消息且在 pending 状态判断），并移除了 system-panel 下方多余的气泡操作栏图标（播放/编辑/复制等）。修改了 chatView.js 和 chat.css" 
## 2024-05-30: 修复了 chatView.js 中因为意外覆盖导致的语法报错，恢复了正常气泡的渲染逻辑。 
"## 2024-06-24: 修复了发送取消无效的Bug，实现了输入框自适应高度与发送按钮图标化，并统一了系统面板宽度。" 
## 2024-05-30: 修复了 chatView.js 中的语法错误，恢复了发送按钮的主题色。 

## 2026-06-25: 新建独立 Code Agent 模块（agent.py）并端到端接入前端
新增 agent.py（不堆进 server.py），实现 api-mode 自驱编码 agent，并把前端 codeAgentHubView/codeAgentView 从假数据接到真实后端。

核心设计：
- 上下文管理（不污染角色记忆系统）：独立 SQLite（data/agent.db，tasks/turns/checkpoints 三表）+「进度卡 checkpoint + 滑动窗口」。每轮强制刷新进度卡（不依赖模型自发输出），下一轮开头注入 → 解决「工具调用后不输出消息、下一个接手的 AI 没进度」的问题。进度卡用便宜的 summary_api(flash) 单独生成，LLM 失败也有兜底写入。
- 自测试：每个任务一个隔离沙箱工作区（data/agent_workspaces/<task_id>/），所有编码工具（含 run_terminal_command）都 root 在此，agent 能写代码后真正跑测试自验、且碰不到真实项目。沙箱越权路径被拦截。
- server.py 仅加薄路由 /api/agent/*，启动时 agent.init_db()。

## 2026-06-25: Code Agent 加子 agent 编排(map-reduce) + 共享限流器，规避 429
- spawn_subagents 工具：规划器(贵模型)把任务拆成多个【只读分析】子任务，并发派发给 worker（便宜高 RPM 的 worker_api 模型，未配置则回退到 summary_api/flash），汇总成一个包一次性回喂规划器，大幅减少贵模型调用次数。
- RateLimiter：按 endpoint 分别滑动窗口限流（api/summary_api/worker_api 各走各的额度池），所有 LLM 调用都过它，避免并发把端点打到 429。
- worker 只读（read_file_with_lines/grep_files/glob_files），并发零写冲突；写入由规划器统一做。
- config.example.json 增加 worker_api 段（base_url/api_key/model/rpm）。

## 2026-06-25: Code Agent 真中断 / 排队补充 / 调试可观测 / 提示词 SOP
- 真中断：request_cancel + 循环检查点（每轮开头、每个工具后）真正停后端循环，状态置「已挂起」、保存进度卡、可恢复继续。路由 /api/agent/interrupt，前端停止键带二次确认。
- 排队补充(queue)：agent 工作时用户可发消息（绿色 queue 键 / 回车），不打断，持久化为用户消息 + 入队，下一轮开头自动并入上下文。路由 /api/agent/enqueue。
- 调试可观测：agent 内置 500 条环形日志（LLM 调用/限流等待/工具调用与结果/并发 worker 用时/进度卡/队列/中断/错误），经 /api/logs 暴露，前端 vConsole 轮询彩色显示（替换原 Mock）。
- last_llm_payload：每次发主模型前捕获完整 messages，/api/agent/last_prompt 暴露；前端任务头部齿轮按钮一键 dump 到 vConsole。
- 系统提示词按「程序员」预设 SOP 重写：第一轮强制先规划（复述需求/拆 Todo/选方案/写 work_log），后续增量开发 + 每改一处立即跑验证，never 未验证就声称完成。
- 工具调用上限 25 → 99（由限流器兜 429）。

## 2026-06-25: Code Agent 工具实用性增强 + 工作区文件树
- 工作区文件树：每轮把任务沙箱的目录树注入 agent 上下文（greenfield 从空长出，brownfield 看种入的代码）；前端 Server Workspace 面板也改为显示完整沙箱树（/api/agent/task 返回 tree）。
- run_terminal_command 超时 30s → 默认 180s 且可传 timeout（上限 1800s），装包/构建/测试套件不再被掐断。
- 种现有项目进沙箱：新建任务可填源目录绝对路径，copytree 进沙箱（忽略 .git/node_modules/__pycache__ 等），agent 在隔离副本上改现有代码。解决「空沙箱不知道现有结构」。
- 新工具：glob_files（通配符列文件/看结构，主 agent 与只读 worker 均可用）、replace_in_file（按 old_string→new_string 精确替换，带唯一性校验/replace_all，比纯行号 apply_file_edits 稳）；read_file_with_lines 支持 offset/limit 范围读，应对大文件。

注意：本机文件编辑工具(IDE/同步层)会偶发在写入后异步截断较大/CRLF 文件（本次多次发生于 codeAgentView.js / codeAgentHubView.js / api.js / config.example.json），均已用 bash 整文件重写并逐一通过 py_compile / node --check / json 校验修复。如再发现文件莫名变短，整文件重写即可。
