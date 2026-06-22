# -*- coding: utf-8 -*-
"""消息推送通道：解决 iOS PWA 无法后台/无原生通知的问题。
server 在「主动消息 / 定时任务到点 / 用户离线时收到回复」等时机调用，把通知推到手机。
当前主推 Bark(iOS)，并预留 ntfy(自托管/安卓) 与 SMTP(邮件) 接口。
纯 server 能力，不依赖聊天模型是否支持 tool calling。
"""
import json
import smtplib
import urllib.request
from email.mime.text import MIMEText


def send_notification(cfg, title, body, *, jump_url=None):
    """按 config['notify'] 派发一条推送。返回 (ok: bool, detail: str)。
    未启用或未配置时返回 (False, 原因)，绝不抛异常——推送失败不该影响主流程。"""
    if not cfg or not cfg.get("enabled"):
        return False, "通知未启用（config.notify.enabled=false）"
    channel = (cfg.get("channel") or "bark").lower()
    try:
        if channel == "bark":
            return _send_bark(cfg.get("bark") or {}, title, body, jump_url)
        if channel == "ntfy":
            return _send_ntfy(cfg.get("ntfy") or {}, title, body, jump_url)
        if channel == "smtp":
            return _send_smtp(cfg.get("smtp") or {}, title, body)
        return False, f"未知推送通道: {channel}"
    except Exception as e:
        return False, f"{channel} 推送异常: {e}"


def _post_json(url, payload, timeout=15):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except Exception:
        return {"_raw": raw}


def _send_bark(b, title, body, jump_url):
    """Bark：装 iOS App 拿 device_key，server POST 到 /push 即弹原生通知。
    自托管 Bark 改 base_url 即可。"""
    key = (b.get("device_key") or "").strip()
    if not key:
        return False, "Bark device_key 未配置（在 Bark App 里复制你的 key 填进 config.notify.bark.device_key）"
    base = (b.get("base_url") or "https://api.day.app").rstrip("/")
    payload = {
        "device_key": key,
        "title": title,
        "body": body,
        "group": b.get("group", "ChatBridge"),
    }
    if b.get("sound"):
        payload["sound"] = b["sound"]
    if b.get("icon"):
        payload["icon"] = b["icon"]
    if jump_url:
        payload["url"] = jump_url
    d = _post_json(base + "/push", payload)
    code = d.get("code")
    if code == 200:
        return True, "Bark 已发送"
    return False, f"Bark 返回: {d.get('message', d)}"


def _send_ntfy(n, title, body, jump_url):
    """ntfy.sh / 自托管 ntfy：往 topic 发即可。Title 头用 RFC2047 编码兼容中文。"""
    topic = (n.get("topic") or "").strip()
    if not topic:
        return False, "ntfy topic 未配置"
    base = (n.get("base_url") or "https://ntfy.sh").rstrip("/")
    from email.header import Header
    headers = {"Title": Header(title, "utf-8").encode()}
    if jump_url:
        headers["Click"] = jump_url
    if n.get("token"):
        headers["Authorization"] = f"Bearer {n['token']}"
    req = urllib.request.Request(base + "/" + topic, data=body.encode("utf-8"), headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        r.read()
    return True, "ntfy 已发送"


def _send_smtp(s, title, body):
    """邮件兜底：任何能收邮件的设备都行。建议用 465 SSL。"""
    host = s.get("host")
    port = int(s.get("port", 465))
    user = s.get("user")
    pwd = s.get("password")
    to = s.get("to") or user
    if not (host and user and pwd and to):
        return False, "SMTP 配置不全（host/user/password/to）"
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = title
    msg["From"] = user
    msg["To"] = to
    if port == 465:
        srv = smtplib.SMTP_SSL(host, port, timeout=20)
    else:
        srv = smtplib.SMTP(host, port, timeout=20)
        srv.starttls()
    try:
        srv.login(user, pwd)
        srv.sendmail(user, [to], msg.as_string())
    finally:
        srv.quit()
    return True, "邮件已发送"
