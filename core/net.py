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
