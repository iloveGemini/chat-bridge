# -*- coding: utf-8 -*-
"""路由注册表：用 @post/@get 装饰器把各域路由登记进表，由 Handler 统一分发。

路由函数签名统一为 fn(h, query, session, session_id)：
- h          : Handler 实例，提供 h._read_json() / h._json() / h.headers / h.rfile
- query      : parse_qs 解析的查询参数 dict
- session    : 已解析的 ChatSession（NO_SESSION_* 路径为 None）
- session_id : 会话 id 字符串
分发命中返回 True；未命中返回 False，调用方回落到旧的 if/elif 链（增量迁移期）。
"""
_POST = {}
_GET = {}


def post(path):
    def deco(fn):
        _POST[path] = fn
        return fn
    return deco


def get(path):
    def deco(fn):
        _GET[path] = fn
        return fn
    return deco


def dispatch_post(h, path, query, session, session_id):
    fn = _POST.get(path)
    if fn is None:
        return False
    fn(h, query, session, session_id)
    return True


def dispatch_get(h, path, query, session, session_id):
    fn = _GET.get(path)
    if fn is None:
        return False
    fn(h, query, session, session_id)
    return True
