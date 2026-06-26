# -*- coding: utf-8 -*-
"""routes —— HTTP 路由层：按域拆分的路由模块 + 统一分发。

import 各子模块即触发 @post/@get 注册；server 的 Handler 调用 dispatch_* 命中即处理。
"""
from routes.registry import dispatch_post, dispatch_get
from routes import agent_routes  # noqa: F401  （import 触发注册）
from routes import chat_routes  # noqa: F401

__all__ = ["dispatch_post", "dispatch_get"]
