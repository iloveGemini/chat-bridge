# -*- coding: utf-8 -*-
"""DevTeam —— 专门开发本项目（Chat Bridge）的多角色团队 agent。

与通用 coding 组并存、互不影响。七个角色（Manager/Architect/Designer/
Context Engineer/Programmer/Checker-Tech/Checker-Design）围绕：
  - 六层项目状态树 + 状态机（单一写入口 ProjectStateStore.apply）
  - 四层消息协议（Task/Report/Decision/Block）
  - 双路由（Manager 自动 Intake + @角色 手动）
协作完成开发。元数据全部进 SQLite（runtime/devteam_store.py）。
"""
