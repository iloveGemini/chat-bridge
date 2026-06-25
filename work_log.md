## 计划与执行
- [x] Step 1: 在 `frontend/js/views/codeAgentHubView.js` 的 `els()` 中添加 `globalInstInput` 和 `globalInstSave` 元素的获取。
- [x] Step 2: 在 `bindOnce()` 中为 `globalInstSave` 添加点击事件，将输入框的内容保存到 `localStorage` 中。
- [x] Step 3: 在 `open()` 中读取 `localStorage` 并恢复输入框的内容。
- [x] Step 4: 在创建新任务时，读取 `localStorage` 中的全局约束，并将其追加到任务的 `goal` 中，从而使其被放进 Agent 的提示词中。

## 状态
- [x] 任务完成
