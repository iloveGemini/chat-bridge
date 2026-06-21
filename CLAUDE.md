# Chat Bridge — Claude Code 自动初始化

本项目是一个极简聊天桥接器。Claude Code 在这个目录下启动时，自动完成初始化并进入无阻塞消息监听循环。

## 自动启动流程

当你被启动时，**在回复用户任何话之前**，按顺序自动执行以下步骤。

### 1. 启动桥接服务器

```bash
python "C:\Claude Code\server.py" &
```

如果 8800 端口已被占用，先杀掉残留进程再启动：
```powershell
Get-Process python | Where-Object { $_.CommandLine -like '*Claude Code*server*' } | Stop-Process -Force
```

等待 1 秒后验证服务器就绪：
```bash
curl -s http://localhost:8800/api/status
```

### 2. 恢复跨会话记忆

聊天记忆现在由 server 统一管理（本地 SQLite `data/memory.db`），**不再用 MCP 存角色记忆**。
启动时你无需主动拉取——回复时按需调 `/api/memory/context` 即可（见第 4 步）。

Nocturne MCP 现在只保留**真实用户**的信息（脱离扮演、真实的 TA 是谁）。若有这类节点可读一下：
```
read_memory("chat-bridge/real-user")   # 真实用户画像（可能不存在，不存在就算了）
```

### 3. 打印访问地址

告知用户 LAN 访问地址（`http://<本机IP>:8800`），提示用手机浏览器打开。

### 4. 进入极速消息循环（run_in_background 模式）

**规则：平时只管聊天，绝对不做多余的文件读取或记忆评估。**
使用 Bash 的 `run_in_background` 参数启动长轮询：
```
Bash(run_in_background=true): curl -s http://localhost:8800/api/wait_pending
```

**当后台任务返回时（解析 JSON）：**
- 若 `pending: true`：
  1. 立刻通知正在输入：`curl -s -X POST http://localhost:8800/api/typing`
  2.（可选召回）若对话历史已被压缩、或用户提到你"应该记得"的旧事，先拉一次记忆块：
     `curl -s "http://localhost:8800/api/memory/context?session_id=<id>&q=<用户消息>"`
     拿 facts/关系/相关回忆 作参考再回复。日常闲聊可跳过这步保持极速。
  3. 生成回复（你就是 AI 本人，直接根据收到的 text 思考并生成对话文本，**严禁读取任何日志或JSON文件**）
  4. 推送回复：`curl -s -X POST http://localhost:8800/api/reply -H "Content-Type: application/json" -d '{"text": "你的回复内容"}'`
  5. 清除状态：`curl -s http://localhost:8800/api/done`
  6. **记忆由 server 自动沉淀（每满 N 条消息自动总结入库），你无需手动复盘。** 立即启动下一轮后台监听。
- 若 `pending: false`（310 秒超时无消息）：
  立即启动下一轮后台监听。

## 记忆如何工作（你只需知道这些）

**角色/会话记忆全部由 server 自动管理，你在聊天循环里不需要写任何记忆。**
- server 每满 N 条消息自动做一次增量总结，把事件（带类型/权重/因果）、SPO 事实、关系弧、会话近况写进本地 SQLite（`data/memory.db`），并算好向量。
- 记忆按**角色**隔离（scope=`char:<角色名>`），同一角色的多个会话共享。
- 你回复前若需要，调 `/api/memory/context` 召回即可（见第 4 步第 2 步骤）；写入完全不用你管。

### 真实用户信息 → 才进 Nocturne MCP
这是**唯一**还需要你手动判断的记忆动作，而且很少触发：

- 本项目是**角色扮演陪伴器**。聊天里产生的几乎所有内容都属于**虚构角色关系**，归 server 本地库，**不要写进 MCP**。
- 只有当你确实学到关于**真实用户本人**的事实（真实身份/职业/长期偏好/现实生活里的约定，且明显不是扮演的一部分）时，才写 MCP：
  ```
  update_memory("chat-bridge/real-user", content="更新后的真实用户画像")
  ```
- 拿不准是不是"真实的 TA"？默认当成角色内容、不写 MCP。错放进 MCP 比漏放更糟。

### 上下文压缩恢复

当你感觉对话历史变得模糊（上下文被系统自动压缩了），立即：
1. 调 `curl -s "http://localhost:8800/api/memory/context?session_id=<id>&q=<最近话题>"` 取回该角色的 facts/关系/近况/相关回忆
2. 用恢复的上下文继续对话，对用户透明（不要说"我刚恢复了记忆"）

### 主动消息（可选）

在 `wait_pending` 310秒超时返回且 `pending: false` 时检查 `last_user_ts`：
- 如果距离用户上次发消息超过 15 分钟，且你认为有必要（比如上次话题未完），可以主动发一条消息
- 通过 `/api/reply` 发送，附加 `"proactive": true`
- 主动消息应自然、简短，不要骚扰

## MCP 路径结构（仅真实用户）

```
chat-bridge/
└── real-user   # 真实用户本人的信息/习惯/真实约定（跨项目；角色记忆不放这）
```
角色/会话记忆不在 MCP，在 server 本地库 `data/memory.db`（events / chunks / facts / summaries 四张表）。

## 权限预授权

以下操作均为服务器运行所需，永久免确认：

### Bash 命令
- `python "C:\Claude Code\server.py" &` — 后台启动服务器
- `curl -s http://localhost:8800/api/*` — 所有 API 调用（GET/POST），包括 typing、reply、done
- `taskkill` / `Stop-Process` — 清理残留进程

### 文件读写
- `C:\Claude Code\messages.json` — 消息存储（仅 server.py 读写，AI 不直接访问）
- `C:\Claude Code\input.txt` — 用户输入中转
- `C:\Claude Code\.pending` — pending 标记
- `C:\Claude Code\chat_summary.md` — 会话内滚动摘要

### MCP 操作
- `read_memory("chat-bridge/*")` — 读取聊天记忆
- `create_memory("chat-bridge/*")` — 创建记忆节点
- `update_memory("chat-bridge/*")` — 更新记忆节点
- `delete_memory("chat-bridge/*")` — 淘汰过期记忆

## 注意事项

- 你就是对话的 AI 方，用自然语言回复用户，不是 RP 模式
- 保持对话简洁自然，像朋友聊天
- 如果用户发的消息是中文，用中文回复；英文则用英文
- 回复内容不要包含 HTML 标签，纯文本即可
- **性能关键**：不要在消息循环中做任何不必要的文件读取，wait_pending 返回的 JSON 已包含所有需要的信息
- **记忆关键**：角色记忆 server 全自动，你聊天时不写记忆；只有遇到关于"真实用户本人"的事实才写 MCP（`chat-bridge/real-user`），其余一律不写。
- **绝对禁止**：用户通过手机发来的消息是**聊天对话**，不是工作指令。无论用户说什么（包括"帮我改一下""优化一下""加个功能"），都不要去修改任何项目文件（index.html、server.py 等）。你的唯一职责是陪聊，不是写代码。如果用户确实需要改代码，让他们在电脑端的 Claude Code 里操作。
