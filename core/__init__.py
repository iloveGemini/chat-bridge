# -*- coding: utf-8 -*-
"""core —— 全局共享基础设施（路径常量、配置单例、网络/日志工具）。

模块化拆分的中心层：config/paths/net 等被各领域模块共享。
约定：config 是一个【就地修改】的单例 dict（绝不重新赋值），
所以任何模块 `from core.config import config` 拿到的都是同一个对象。
"""
