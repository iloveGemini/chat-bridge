# -*- coding: utf-8 -*-
"""推送通知 + 局域网地址：探测 LAN IP、解析图标 URL、统一推送入口。

LAN_BASE 是运行时探测的本机地址，用 get/set 访问器避免「from import 绑定到旧值」。
"""
from core.config import config
import notify

_lan_base = ""


def set_lan_base(v):
    global _lan_base
    _lan_base = v or ""


def get_lan_base():
    return _lan_base


def _detect_lan_ip():
    """可靠地拿到本机 LAN IP：用 UDP 连一下外网地址读本地 sockname（不真正发包）。
    比 gethostbyname(gethostname()) 稳，后者常返回 127.0.x.x。"""
    import socket

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "127.0.0.1"


def _resolved_notify_cfg():
    """返回 notify 配置；若 bark.icon 是相对路径（/icons/x.png），补成绝对 URL。
    base 优先用 config.notify.icon_base（可填 https 隧道地址），否则用运行时探测的 LAN_BASE。"""
    import copy

    cfg = copy.deepcopy(config.get("notify") or {})
    base = (cfg.get("icon_base") or "").strip().rstrip("/") or get_lan_base()
    b = cfg.get("bark") or {}
    icon = (b.get("icon") or "").strip()
    if icon.startswith("/") and base:
        b["icon"] = base + icon
        cfg["bark"] = b
    return cfg


def _push_notify(title, body):
    """统一推送入口：解析图标 URL 后发推。返回 (ok, detail)。"""
    return notify.send_notification(_resolved_notify_cfg(), title, body)
