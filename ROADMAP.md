# Chat Bridge · 路线图

> 定位：自用的角色扮演陪伴聊天器。起因是市面同类产品用着不满意，自己造一个。
> 需求大，但**暂不商业化**，成熟后再考虑分享。
> PM 评审结论：**引擎（记忆系统）很强，短板是工程债和产品身份。** 先打地基，别先做花活。

## 优先级（自上而下做）

### P0 · 记忆召回质量可观测 ✅ 已完成（2026-06-21）
核心循环现在靠启发式跑（子串去重 + 关键词兜底 + 向量降级），但没人知道它准不准。
- ✅ 每次召回打印一行日志：模式(vector/keyword)、查询、各段命中数、每条回忆/细节的分数(向量)或 kw✓/kw✗(关键词)、被去重丢弃的细节、空召回告警。
- 开关：`config.json` → `memory.recall_log`（默认 true，想安静就设 false）。
- 下一步可做：把这份诊断接到记忆面板里可视化，而不只是看终端日志。

### P1 · 局域网隐私 + 鉴权 ✅ 已完成（2026-06-22）
现状：LAN 裸 HTTP、无鉴权、明文 SQLite。同一 Wi-Fi 下任何人可打开 `http://<IP>:8800` 看私密 RP 对话。
- ✅ 加了单一访问口令：`config.json` → `auth.{enabled,password}`（当前默认口令 `1221`，**请尽快改掉**）。
- ✅ 机制：口令派生稳定 token（`sha256("chatbridge:"+口令)`，重启不失效）。`/api/login` 校验口令签发 token；前端全局 fetch 拦截器自动注入 `X-Auth-Token`，401 弹登录浮层，token 存 localStorage。
- ✅ 本机 localhost 访问免口令（claude_mode 长轮询 / 本地浏览器照常用），只有 LAN 设备需要登录。
- 注意：仍是明文 SQLite + HTTP（非 HTTPS）。口令解决了「门虚掩」，传输/落盘加密留待后续。

### P2 · 拆单体 + 回归测试 🟡 进行中（2026-06-22）
index.html 3300 行、server.py 1361 行单文件，零自动化测试。
- ✅ 给 `memory_store` 补了一组回归测试：`tests/test_memory_store.py`（迁移/写入/召回/去重/降级 + fork/migrate/delete 作用域 + 前缀防串，共 16 例）。跑法：`python tests/test_memory_store.py`。
- ⬜ 把 index.html 拆成几个模块（渲染 / 状态 / 抽屉 / 记忆面板）——改动大、有凿坏现有 UI 的风险，下轮单独做。
- 依据：「输入框 flex:1」那类小 bug 反复翻车，就是单体 + 无测试的利息。

### P3 · Artifacts-Lite（往后放）
聊天里用 sandbox iframe 渲染 AI 生成的交互网页（方案见 `to do.txt`）。
- 技术酷、XSS 已用 `sandbox="allow-scripts"` 焊死。
- 但属 shiny feature——等核心循环能量化「被记住」的体验后再加糖。

## 已完成
- 输入框多行自适应增高（移除 `flex:1`，改 JS 驱动 height，上限 40vh）
- 气泡模式：AI 多行回复拆多气泡，主题下开关，localStorage 持久化
- recall【细节】按 RP 角色名 / 用户名标注（chunks 表加 speaker 列，含自动迁移）
