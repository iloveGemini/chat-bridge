# Chat Bridge —— 模块化生态架构（设计草稿）

> 本文沉淀「多 Agent 生态」的架构决策。当前 server 模块化重构已完成（server.py 3754→481 行，
> 全部路由迁入 `routes/`，领域拆分为 core/session/prompts/memory/chat/agents/tools/routes），
> 本文档规划在此基线之上的下一步演进。
>
> 状态：设计已收敛，待实现。实现顺序见文末「Roadmap」。

---

## 0. 统领思想

一切按 **agent_type 策略化**。定义一个 `BaseAgent` 抽象，挂四个可插拔件：

| 插槽 | 职责 | 对应章节 |
|------|------|----------|
| `prompt_assembler` | 按固定骨架组装提示词 | §2 |
| `context_provider` | 提供 Data / Memory / Worldbook 注入 | §3 |
| `tool_grant` | 该 agent 默认可用的工具集 | §4 |
| `output_schema` | 输出格式规范 + 解析器 + 渲染器 | §1 |

`Manager` 负责实例化这些 agent 并在它们之间路由（§5）。
**骨架固定、槽位内容按 agent 提供** —— 这是贯穿全文的核心模式。

---

## 1. 模块化输出（Output Format）

### 决策
- 输出格式是独立关注点，**从 Post 中拆出**，单独成模块。
- 采用 XML 标签让 AI 做结构化输出；前端按 tag → CSS 类渲染。
- 它不是「世界书式触发桶」，而是 **每个 agent 绑定一个 format profile**（类似 tools 的 RBAC 绑定）。

### 关键：format profile 是「双面同源」的
一个 profile 必须把「给 LLM 的规范」和「读回来的解析/渲染」打包在一起，否则迟早漂移：

```
OutputProfile = {
    prompt_fragment:  str,          # 注入提示词的格式规范（告诉 LLM 怎么吐）
    tag_schema:       [TagDef...],   # 标签词表（含每个标签的属性、是否 hidden）
    parser:           fn(raw)->[seg] # 容错解析：raw -> [{type, attrs, content}]
    renderer_map:     {type: render} # 前端：每种 type 对应的渲染器/CSS 类
}
```

### 硬性约束
- **容错解析必备**：现代模型遵守格式大体没问题，但要兜 5% 边角——` ```xml ` 围栏、
  内容含 `<`/引号未转义、流式半截标签。沿用现有 `parse_msg_envelope` 的「解析失败退回原文」哲学。
- **降级开关**（per-agent / per-session）：笨模型可关标签 → AI 出纯文本 → 前端退回纯气泡渲染。
  开关必须**前后端联动**，别去等永不到来的标签。
- **`<thinking>` hidden 槽**：若启用思考链（§2），tag_schema 必须包含一个 hidden 的 `<thinking>`，
  解析器直接 strip / 前端折叠成「Thought progress」面板，避免思考污染结构化正文。

### XML 还是 JSON —— 按用途分（这本身就是一条一致规则）
- **可见 / 要渲染 / 流式的正文**（chat 气泡、scene、thinking、给用户看的 diff 面板）→ **XML 标签**。
  理由：① 可**增量流式解析**（JSON 必须整个对象合法才能 parse，半截没法渲染）；② 散文里的换行/引号/
  emoji/`<`/`{` 在 JSON 里要全转义、模型常错且一错全废，XML 标签之间就是散文；③ **局部容错**（坏一个标签
  能逐标签正则捞回，JSON 坏一字符全军覆没）。
- **结构化、给机器消费、不流式的数据**（总结/记忆抽取，如 `run_summary` 的 events/facts）→ **JSON**。
- **tool 调用** → 原生 function calling（JSON，走 API 的 `tool_calls` 字段，**不经过本文本解析器**）。
- 误区：「为了和 tool 统一就全用 JSON」——tool 压根不走文本解析器，统一是错觉。真正的一致规则是
  **「结构化给机器 → JSON；要渲染给人 → XML」**。

---

## 2. 模块化提示词（Prompt Assembly）

### 词表（统一 RP 与 coding 两套 agent 共用）
`Main / World·Env / Role / User / Data / Memory / Tone·Style / Post / Reasoning_Scaffold / Output_Format`

- 默认提示词放 `prompts/_defaults/<agent_type>/`（**只读**）；用户改的放 `prompts/<agent_type>/`。
- `get_prompt` 三级覆盖：**default → preset → instance（每会话/每任务绑定）**。
- 默认不可改、可复制后改（类似 dotfiles：注意 app 更新默认值不会回灌已复制的副本）。

### 组装骨架（固定顺序 + 槽位可缺省 + per-agent 填充）

```
[System 头]   Main → World·Env → Role → User → Memory(position=before)
[History]     ……真实多轮 user/assistant turns（即 Data，保持消息数组，绝不文本化）……
[Tail]        Status → Memory(position=after) → Tone·Style → Post → Reasoning_Scaffold(可选) → Output_Format
[末轮]        Last_User_Input
```

- **Data = 真实 messages 数组**，不是塞进 system 的文本坨（否则丢失模型依赖的轮次结构）。
- **Memory 按 position 分流**（详见 §3）：常驻型进 System 头，召回型贴 Tail——
  躲开「lost in the middle」（模型对头尾注意力强、中间弱）。
- **Status 槽（当前/历史状态）**：不要特判 scene。scene 只是 Status 的一个填充者；coding 的
  summary 同理也是 status 类。规划：状态像 memory 一样进 DB 管理；**输出标签带 `category="status"` 属性**，
  凡此属性的输出下一轮回填进 Status 槽（形成 输出→状态持久化 闭环）。第一刀只留槽、现状由 scene 填充。
- 空槽**干净跳过**，不输出空 `<memory></memory>`。
- **不同 agent 不同的 Data/Memory 组装** = 调 `agent.build_data(ctx)` / `agent.build_memory(ctx)`，
  骨架顺序全局固定，内容各自提供。

### 思考链（Reasoning_Scaffold，可选）
- chat agent 关或轻量；coding 等标准化流程打开。
- **放 Tail、紧贴 Output_Format 之前**（CoT 铁律：想完紧接着产出）。
- **必须配套 §1 的 hidden `<thinking>` 标签**，否则污染输出。
- 注意：coding 的 **5 阶段 orchestrator（Plan→Search→Code→Write→Check）本身就是一条被代码固化、
  确定性的思考链**，比 prompt CoT 更强。prompt CoT 只是相位内部的更细一层。

### 思考链是 per-model 策略，不是万能 trick（详见 §6 model_caps.reasoning）
- `reasoning=native`（o1/r1/gpt5-thinking）：**别注入 CoT scaffold**（会双重思考、更慢）；它的隐藏推理
  通道 prefill `<thinking>` 也卡不住。想压就用 API 的 `reasoning_effort` 旋钮或换非思考变体。
- `reasoning=prompt`（不带思考的模型）：scaffold 在这才有价值；`supports_prefill` 为真时再叠 prefill 增强。
- **prefill-hijack（酒馆那招）仅作可选增强，绝不依赖**：Anthropic 支持 assistant prefill、OpenAI 兼容接口
  基本不支持；新模型（如 gpt-5.5）出于反蒸馏/对齐**主动拒绝**外部思考链，趋势只会更强。
- **标准化产出的真抓手 = orchestrator 相位（确定性）+ output schema 校验重试**，不依赖模型配合 CoT。
  schema 级强制（要求可见输出含 `<plan>`→`<answer>` 等必需段、不合则重试）对 native 思考模型同样适用。

---

## 3. 统一注入管道（Worldbook + Memory 合流）

### 洞察
Worldbook 和 Memory 本质是同一个东西——**「按条件注入上下文的片段」**，只是来源不同。
统一成一条管道，二者只是喂给它的两个 source。

### 可注入项数据结构（worldbook 条目 / memory 事实 / 召回事件 通用）

```
Injectable = {
    content:  str,
    trigger:  "always_on" | "keyed" | "semantic",  # 何时进（触发方式）
    position: "before" | "after",                   # 进哪个强注意力区（相对 history）
    priority: int,                                   # 同一桶内排序
    source:   "worldbook" | "memory" | ...           # 来源（可观测/调试用）
}
```

### Assembler 流程
1. **解析 trigger** → 选出本轮命中的项（关键词命中 / 语义召回 / 常驻）。
2. **按 position 分桶** → before-slot（System 头）、after-slot（Tail）。
3. **桶内按 priority 排序** → 渲染进对应 slot。

### 注意
- **trigger 与 position 正交**，别耦合（别默认「召回的就一定在后面」）。
  典型 2×2：核心人设 = `always_on + before`；场景细节 = `keyed + after`。
- 桶内排序**复用现有 lore 的 `priority`**，别造新字段。
- **新增字段**：给 worldbook 条目和 memory 项都加 `position`（Before_Message / After_Message）。
- 推论：§0 的 `context_provider` 退化为「喂不同 source + 设默认 position 策略」，又省一层。

### 实现现状（已落地）
- **lore（世界书）**：每条带 `position` 字段（DB 列，默认 `after`）；`add_lore`/`update_lore` 可设；
  `build_memory_context` 按每条 position 分桶。
- **memory 段**：`memory_store.SECTION_POSITION` 表统一映射——`fact_graph`/`relation_arc`/`recent_state`
  → before（常驻，进系统头），`episodic_memory_chain`/`original_dialogue` → after（召回，贴尾部）。改表即调。
- **回落保护**：`build_memory_context(before_out=None)` 时 before 段回落 after，旧调用不丢内容。
- **管道**：`build_injected_memory` 返回 `{before, after}` → PromptAssembler 的 `Memory_before`(系统头,
  `<persistent_memory>`) / `Memory_after`(尾部, `<recalled_memory>`) 两槽。
- **未做**：trigger 轴的语义/关键词统一、priority 跨 source 统一排序、前端 before/after 选择 UI。

---

## 4. 工具模块化（Tools）—— 现状即设计

当前 `tools/registry.py` 已实现，基本到位，后续只做小幅泛化：
- 工具库 + 按角色 RBAC 默认授权（`ROLE_PERMISSIONS`）+ 会话级用户自定义授权（`session/tools.py` 的 tools.json）。
- 演进：把「role」升级为「agent_type」；把两套 RBAC（registry 子角色权限 + session tools.json）
  统一成一个模型：**agent_type → 默认授权集，用户对 agent 实例做覆盖**。
- 工具实现保持纯函数 + schema（`get_schema`/`execute`），与 agent 解耦。

---

## 5. Agent 生态与 Manager

### BaseAgent
统一抽象，挂 §0 的四个插槽。coding / RP（待重写的「设定助手」可作第二个具体 agent，用来检验抽象）。

### Manager —— 本地优先，留扩展
先用**确定性死代码路由**，但把「接口」和「信封」现在就定死，实现可 later：

1. **路由接口**
   ```
   class BaseManager:
       def route(self, context) -> Next     # 返回 下一个 agent / 结束 / 转人工
   ```
   现在写 `StaticManager`（if/else 死代码）；以后 `LLMManager(BaseManager)` 同接口换 LLM 决策。
   （与 orchestrator 里 `chat_fn` 注入同一套路。）

2. **交接信封**（比路由实现更重要）
   - `AgentContext`：在 agent 间流转的状态信封，由现有 `CodingState` 泛化而来。
   - agent 返回结果协议：
     ```
     AgentResult = { status: "done" | "need_handoff" | "need_user", output, next_hint }
     ```
   - manager 据 `AgentResult` 决策。这样将来静态 manager 换成 LLM manager，**agent 一行都不用动**。

### 失败模式预警
**最大的坑：在只有一个真实 agent 时，就照着想象中的三个 agent 去抽象。**
接口要从手上已有的两个具体 agent（coding + RP）归纳，不要凭空设计第三个。

---

## 6. 模型与推理参数（Model & Sampling）

### 两层分离 —— 别把所有参数都塞预设
```
模型描述符 (per model/endpoint)   ← 能力 & 硬限制，不是给用户调的
  model_caps = {
      supports_stream, supports_top_k, reasoning: "native"|"prompt"|"off",
      supports_prefill, max_context, ...
  }

预设/agent 值 (per use)            ← 用户想调的
  sampling = {
      temperature, top_p, max_tokens, stream, reasoning_effort,
      extra: {}   # 厂商特有参数透传（top_k / min_p 等）
  }
```

### resolve（调用时合并 + 裁剪）
```
resolve(sampling, model_caps):
    丢掉模型不支持的参数（top_k 不支持就不发 → 否则 400）
    clamp 越界（max_tokens 不超 max_context）
    stream = sampling.stream AND model_caps.supports_stream
    reasoning=native → 忽略 prompt-CoT，必要时改用 reasoning_effort
```

### 参数集（收敛到最小够用）
- 主面板：`temperature` / `top_p` / `max_tokens` / `stream` / `reasoning_effort`
- **不收 top_k 作默认**：OpenAI 兼容接口基本只认 top_p，top_k 仅部分支持 → 走 `extra` 透传。
- `temperature` 与 `top_p` 别同时激进调（会打架，惯例只调其一）。
- frequency/presence penalty：进阶可选，先不上主面板。

### 覆盖链（与提示词同源）
sampling 跟提示词走同一条 **default(agent_type) → preset → instance** 覆盖链：
coder 默认 temp≈0.2、RP 默认 temp≈0.8，preset 再覆盖。对纯聊天，"preset 即切换单位"成立；
对多 agent，它就是这条链上的一环。

---

## Roadmap（建议实现顺序）

1. **§2 提示词组装**（最具体、解锁其它）：固定骨架 assembler + `prompts/_defaults` + 三级 `get_prompt`。
   **第一刀建议：把现有 RP 的 build_header/tail 重构进 PromptAssembler（行为不变、可 diff 验证），
   骨架带上 before/after/Output_Format 空槽（先 no-op）。** 沿用 server 重构那套「增量、零行为变化、每步验证」。
2. **§6 模型参数 resolve 层**：model_caps + sampling 两层 + resolve 裁剪 + extra 透传（小而独立，早做不亏）。
3. **§3 统一注入管道**：给 worldbook/memory 加 `position`，合流到一条 trigger×position×priority 管道
   （此时 §2 的 before 槽已就位，position=before 才有地方落）。
4. **§4 工具**：role→agent_type 泛化 + 两套 RBAC 统一（基本现成）。
5. **§1 输出格式**：format profile（双面同源）+ 前端 type→renderer 注册表 + 降级开关 + schema 校验重试。
6. **§5 Agent 生态**：定 `BaseAgent` 四插槽 + `BaseManager.route` + `AgentContext`/`AgentResult`；
   先 `StaticManager`，留 `LLMManager` 扩展位。
7. 以**重写「设定助手」**作为第二个具体 agent，回灌检验以上所有抽象。

---

## 附：与当前代码的对应关系

| 设计概念 | 现有落点 / 演进方向 |
|----------|---------------------|
| BaseAgent 四插槽 | `agents/base.py`（已占位，待填充） |
| coding 思考链 | `agents/coding/orchestrator.py` 的 5 相位（已实现，即确定性 CoT） |
| prompt 解耦 | `agents/coding/prompts/*.md` + `prompts/prompts.py`（已解耦，待加 _defaults/三级覆盖） |
| 统一注入管道 | `memory/memory.py` 的 `build_injected_memory` + `memory_store` 的 lore（待合流 + 加 position） |
| Tool RBAC | `tools/registry.py` ROLE_PERMISSIONS + `session/tools.py`（已实现，待统一） |
| Manager 雏形 | `server.py` `/api/agent/send` 网关 + `routes/`（静态路由雏形，待泛化为 BaseManager） |
| AgentContext | `agents/coding/state.py` `CodingState`（待泛化） |
| 输出信封 | `chat/envelope.py` `parse_msg_envelope`（已有容错解析，待泛化为 output profile） |
| 模型参数/采样 | `core/config.py` 的 api/summary_api + `chat/llm.py` 的 `_chat` 调用（待抽出 model_caps + resolve 层） |
