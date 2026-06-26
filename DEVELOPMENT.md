# Chat Bridge 项目开发说明书

本说明书旨在指导开发者（人类或 Agent）理解 Chat Bridge 的架构设计、模块职责，并规范新功能（如新 Agent 或新 Tool）的开发流程。

## 1. 项目架构概述

项目采用**模块化生态架构**，核心思想是“一切皆策略”。通过将复杂的逻辑拆分为独立的领域模块，实现了高内聚低耦合。

### 核心目录结构
- **`core/`**: 基础核心层。包含全局配置 (`config.py`)、路径常量 (`paths.py`) 和网络/工具函数 (`net.py`)。
- **`agents/`**: 智能体层。定义了 `BaseAgent` 基类和 `Manager` 路由机制。
- **`tools/`**: 工具池。按领域拆分的原子工具（如 `coding`, `rp`, `common`），支持 RBAC 权限控制。
- **`prompts/`**: 提示词管理。包含 `PromptAssembler`（提示词组装器）和各类角色提示词模板。
- **`memory/`**: 记忆系统。负责长短期记忆的存储、向量检索、增量总结及世界书（Lore）注入。
- **`chat/`**: 聊天链路。处理 LLM 调用、信封解析（XML 标签）、场景管理、TTS 和主动外联。
- **`routes/`**: 路由层。将 HTTP 接口按领域拆分（如 `agent_routes.py`, `chat_routes.py`）。
- **`session/`**: 会话管理。维护会话状态、工具授权开关及上下文。

---

## 2. Agent 开发指南

Agent 是处理特定任务的逻辑单元。项目支持多 Agent 协同，由 `Manager` 负责调度。

### 如何开发一个新的 Agent
1.  **继承基类**：在 `agents/` 下创建新模块，继承 `agents.base.BaseAgent`。
2.  **定义属性**：
    - `agent_type`: 唯一的字符串标识。
    - `default_tool_grant()`: 返回该 Agent 默认拥有的工具组名（如 `["coding", "web"]`）。
    - `prompt_descriptor()`: (可选) 返回用于前端配置的提示词 ID 和默认文本。
3.  **实现核心逻辑**：
    - 实现 `run(ctx: AgentContext) -> AgentResult` 方法。
    - `AgentContext` 包含 `session_id`, `history`, `user_msg` 等上下文。
    - `AgentResult` 需返回状态（`done`, `need_handoff`, `need_user`）及输出内容。
4.  **注册 Agent**：在 `agents/manager.py` 中导入并确保其被注册到 `_AGENTS` 字典中。

### 典型案例：Coding Agent
- **位置**: `agents/coding/orchestrator.py`
- **模式**: 5 阶段状态机（Plan → Search → Code → Write → Check）。
- **特点**: 每个阶段使用不同的角色（Role）和工具权限，通过 `CodingState` 传递中间产物。

---

## 3. Tool 开发指南

工具是 Agent 与外部世界交互的唯一途径。

### 如何添加一个新的 Tool
1.  **创建文件**：在 `tools/<category>/` 下创建新的 `.py` 文件（例如 `tools/common/my_tool.py`）。
2.  **实现接口**：
    - `get_schema()`: 返回符合 OpenAI Function Calling 规范的 JSON Schema。
    - `execute(args, context)`: 实现具体的执行逻辑。`context` 中包含 `session_id` 和安全路径解析回调。
3.  **自动加载**：`tools/registry.py` 会自动扫描子包并加载含有上述接口的模块。
4.  **配置权限 (RBAC)**：
    - 在 `tools/registry.py` 的 `ROLE_PERMISSIONS` 中，将工具名分配给对应的角色（如 `planner`, `developer`）。
    - 如果是新类别，可能需要更新 `TOOL_GROUPS`。

---

## 4. 提示词与输出规范

### 提示词组装 (Prompt Assembly)
项目使用 `prompts/assembler.py` 统筹提示词结构，固定骨架如下：
- **System 头**: `Main` → `World/Env` → `Role` → `User` → `Memory(before)`
- **History**: 真实的消息数组（保持多轮对话结构）。
- **Tail**: `Status` → `Memory(after)` → `Tone/Style` → `Post` → `Reasoning` → `Output_Format`

### 输出信封 (Output Envelope)
- **原则**: 给人看的/流式的用 **XML 标签**；给机器看的/非流式的用 **JSON**。
- **常用标签**:
    - `<msg><content>...</content></msg>`: 标准对话信封。
    - `<thinking>`: 思考链（前端自动折叠）。
    - `<scene id="..." time="..." place="..." />`: 场景转场标记。

---

## 5. 记忆与上下文注入

### 注入管道
- **位置**: `memory/memory.py` 中的 `build_injected_memory`。
- **策略**:
    - `position="before"`: 注入到 System 提示词头部（适合常驻设定、核心事实）。
    - `position="after"`: 注入到历史记录之后（适合召回的事件、相关回忆）。
- **来源**: 统一处理 `worldbook` (手动录入) 和 `memory` (自动总结)。

---

## 6. 运行与调试

- **入口文件**: `server.py`。
- **启动命令**: `python server.py` (默认端口 8800)。
- **状态检查**: 访问 `http://localhost:8800/api/status`。
- **日志**: 关键逻辑使用 `core.net.log_print` 输出，支持前端 `/api/logs` 实时查看。

---

**开发者注意**：在修改代码前，请务必阅读 `ARCHITECTURE.md` 以了解最新的架构决策，并参考 `work_log.md` 确认当前的开发进度。