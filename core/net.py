# -*- coding: utf-8 -*-
"""网络与日志工具：带重试的 JSON POST、时间戳日志、安全解码。"""
import json
import time
import urllib.request
import urllib.error


def log_print(*args, **kwargs):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}]", *args, **kwargs)


def _http_post_json(url, payload, headers, timeout=90, max_retries=5, tag="LLM"):
    """POST JSON 并解析返回。对 429 / 5xx / 连接超时做指数退避重试（优先尊重 Retry-After 头），
    重试用尽仍失败则抛出最后一次异常，交调用方兜底。免费代理的瞬时限速不再一次就放弃。"""
    delay = 2
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode("utf-8"), headers=headers
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_exc = e
            retryable = (e.code == 429) or (500 <= e.code < 600)
            if not retryable or attempt >= max_retries:
                raise
            wait = delay
            ra = e.headers.get("Retry-After") if e.headers else None
            if ra:
                try:
                    wait = max(wait, float(ra))
                except Exception:
                    pass
            log_print(
                f"⏳ [{tag}] {e.code} 限速/服务端忙，{wait:.1f}s 后重试（{attempt + 1}/{max_retries}）"
            )
            time.sleep(wait)
            delay = min(delay * 2, 8)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_exc = e
            if attempt >= max_retries:
                raise
            log_print(
                f"⏳ [{tag}] 连接异常（{e}），{delay:.1f}s 后重试（{attempt + 1}/{max_retries}）"
            )
            time.sleep(delay)
            delay = min(delay * 2, 8)
    if last_exc:
        raise last_exc


def _safe_decode(data):
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc)
        except Exception:
            continue
    return data.decode("utf-8", errors="replace")


def _safe_name(name):
    """只允许字母数字下划线短横线，防止路径穿越。"""
    return "".join(c for c in (name or "") if c.isalnum() or c in "-_")


def _extract_json(text):
    """从 LLM 输出里抠出第一个 JSON 对象（容忍 ``` 围栏和前后废话）。"""
    if not text:
        return None
    import re

    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(t[start : end + 1])
    except Exception:
        return None


# ================= 结构化输出信封：解析 + 场景闩锁 =================
# AI 每轮回复用 <msg> 信封包裹：可选 <scene/> 元数据 + <content> 正文。
# 系统解析后只把 <content> 发前端 / 落库，<scene> 驱动后台时空状态机。
# 容错铁律：信封缺失或标签破损时，绝不报错——剥掉已知标签后整段当正文，宁可丢一次结构化也不让用户收到空消息。
