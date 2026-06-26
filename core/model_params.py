# -*- coding: utf-8 -*-
"""模型能力描述符 + 采样参数 resolve 层（ARCHITECTURE.md §6）。

两层分离：
  model_caps (per model/endpoint)  —— 能力 & 硬限制，决定哪些参数能发、要不要裁剪。
  sampling   (per use/preset/agent) —— 用户想要的值（temperature/top_p/max_tokens/...）。
resolve 时按能力过滤+钳制，避免把不支持的参数发出去被 4xx 拒。

当前为最小可用版：caps 默认保守（非流式、不发 top_k），可由 config.api.caps 覆盖；
sampling 由 调用方默认值 + config.api.sampling 合并而来。行为默认与改造前等价。
"""

# 主面板白名单（这些键若给了就直接透传）
_WHITELIST = ("temperature", "top_p", "max_tokens", "frequency_penalty", "presence_penalty")

DEFAULT_CAPS = {
    "supports_stream": False,   # 当前传输是阻塞式 _http_post_json，尚未接流式 → 默认不发 stream
    "supports_top_k": False,    # OpenAI 兼容接口默认不认 top_k → 不发，避免 400
    "reasoning": "prompt",      # native | prompt | off（详见 §2 思考链策略）
    "supports_prefill": False,
    "max_context": None,
}


def get_model_caps(api_cfg):
    """取该端点的能力描述符。允许 config.api.caps 覆盖默认。"""
    caps = dict(DEFAULT_CAPS)
    caps.update((api_cfg or {}).get("caps") or {})
    return caps


def resolve_sampling(sampling, caps):
    """把期望的 sampling 按 caps 过滤+钳制，返回可直接 ** 进 payload 的 dict。"""
    sampling = sampling or {}
    out = {}
    for k in _WHITELIST:
        v = sampling.get(k)
        if v is not None:
            out[k] = v
    # max_tokens 钳制到模型上限
    if out.get("max_tokens") and caps.get("max_context"):
        out["max_tokens"] = min(out["max_tokens"], caps["max_context"])
    # stream 受能力 gate
    if sampling.get("stream") and caps.get("supports_stream"):
        out["stream"] = True
    # top_k 仅在支持时才发（否则丢弃，避免被拒）
    if sampling.get("top_k") is not None and caps.get("supports_top_k"):
        out["top_k"] = sampling["top_k"]
    # reasoning_effort 透传（仅 native 思考模型有意义，由调用方/配置决定是否给）
    if sampling.get("reasoning_effort") is not None:
        out["reasoning_effort"] = sampling["reasoning_effort"]
    # 厂商特有参数透传
    out.update(sampling.get("extra") or {})
    return out


def build_sampling(api_cfg, default_temp=None):
    """合并 调用方默认温度 + config.api.sampling 覆盖，再按 model_caps 裁剪。
    默认（config 未配 sampling）→ 仅 {temperature: default_temp}，与改造前等价。"""
    sampling = {}
    if default_temp is not None:
        sampling["temperature"] = default_temp
    sampling.update((api_cfg or {}).get("sampling") or {})
    return resolve_sampling(sampling, get_model_caps(api_cfg))
