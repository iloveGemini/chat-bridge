# Work Log

## 任务目标
1. 把本地编程工具从chatSettingView删掉。
2. 检查下工具里的联网工具有没有实现。

## 计划与执行
- [x] Step 1: 在 `frontend/js/views/pluginManagerView.js` 中移除 "本地项目操控 (Coding Agent)" 工具的相关代码（该视图由 `chatSettingsView.js` 打开）。
- [x] Step 2: 检查 `tooling.py` 和 `agent.py` 中是否有联网工具（如 `web_search`）的实现。经检查，目前代码中**没有**实现联网工具。

## 状态
- Status: DONE