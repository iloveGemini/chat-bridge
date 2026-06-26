# -*- coding: utf-8 -*-
"""配置路由：模式切换 / 保存配置 / 测试模型列表 / 测试推送 / 读取配置。"""
import json
import urllib.request

from core.config import config, config_lock, save_config as _save_config, load_config as _load_config
from core.net import _safe_decode, log_print
from chat.notify import _push_notify, _resolved_notify_cfg
from routes.registry import post, get


@post("/api/toggle_mode")
def _toggle_mode(h, query, session, session_id):
    new_mode = "claude_mode" if config.get("mode") == "api" else "api"
    config["mode"] = new_mode
    _save_config()
    h._json({"ok": True, "mode": new_mode})


@post("/api/config/save")
def _config_save(h, query, session, session_id):
    length = int(h.headers.get("Content-Length", 0))
    new_cfg = json.loads(_safe_decode(h.rfile.read(length)))
    with config_lock:
        config.update(new_cfg)  # 深度更新内存中的 config
    _save_config()
    h._json({"ok": True})
    return


@post("/api/test_models")
def _test_models(h, query, session, session_id):
    # ⭐️ 由 Python 后端代理向目标 API 发送请求，完美避开浏览器的跨域 CORS 限制
    length = int(h.headers.get("Content-Length", 0))
    data = json.loads(_safe_decode(h.rfile.read(length)))
    base_url = data.get("base_url", "").rstrip("/")
    api_key = data.get("api_key", "")

    if not base_url:
        h._json({"ok": False, "error": "请先填写 base_url"})
        return

    # 智能兼容：如果用户没写 /models，自动补齐标准 OpenAI 兼容的 /models 路径
    target_url = (
        f"{base_url}/models" if not base_url.endswith("/models") else base_url
    )
    try:
        req = urllib.request.Request(
            target_url, headers={"Authorization": f"Bearer {api_key}"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            res_body = json.loads(resp.read().decode("utf-8"))
            models = [m["id"] for m in res_body.get("data", []) if "id" in m]
            h._json({"ok": True, "models": models})
    except Exception as e:
        h._json({"ok": False, "error": str(e)})
    return


@post("/api/notify/test")
def _notify_test(h, query, session, session_id):
    # 单独跑通推送通道用：往配置的通道发一条测试通知，把成败原样回报前端
    _load_config()
    ok, detail = _push_notify(
        "Chat Bridge 测试推送", "如果你在手机上看到这条，推送通道就通了 ✅"
    )
    icon_url = (_resolved_notify_cfg().get("bark") or {}).get("icon") or "(无)"
    log_print(f"🔔 [推送测试] ok={ok} detail={detail}")
    log_print(
        f"🔔 [推送测试] 图标URL={icon_url} —— 用手机浏览器打开它，能看到图才说明手机够得到"
    )
    h._json({"ok": ok, "detail": detail, "icon": icon_url})


@get("/api/config")
def _get_config(h, query, session, session_id):
    with config_lock:
        h._json(config)
    return

