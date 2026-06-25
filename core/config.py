# -*- coding: utf-8 -*-
"""全局配置单例 + 口令鉴权。

关键约定：`config` 是一个【就地修改】的 dict 单例——load_config 用
clear()+update() 刷新它，绝不重新赋值。这样所有 `from core.config import config`
的模块拿到的始终是同一个对象，避免「重新绑定导致各模块看到旧引用」的经典坑。
"""
import hashlib
import json
import threading

from core.paths import CONFIG_FILE
from core.net import log_print

# 全局配置单例与锁
config = {}
config_lock = threading.Lock()


def load_config():
    """从 config.json 读入并就地刷新 config 单例，再补全缺省项。"""
    global config
    with config_lock:
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                # 就地刷新但【绝不清空】：先删掉已不存在的键，再覆盖。
                # 不能用 config.clear()+update()——那会在两步之间留下「config 暂时为空」的窗口，
                # 别的线程（聊天/总结）不持锁读 config 时会偶发拿到空配置，导致
                # config.get("summary_api")/("api") 返回空 → run_summary 静默早退（只见触发、无结果）。
                for _k in list(config.keys()):
                    if _k not in data:
                        del config[_k]
                config.update(data)
            except Exception as e:
                log_print(f"[警告] config.json 解析失败: {e}")

        # 补全缺省配置
        if "mode" not in config:
            config["mode"] = "api"
        if "api" not in config:
            config["api"] = {}
        config.setdefault(
            "embedding",
            {
                "enabled": False,
                "base_url": "https://api.siliconflow.cn/v1",
                "api_key": "",
                "model": "BAAI/bge-m3",
            },
        )
        config.setdefault(
            "rerank",
            {
                "enabled": False,
                "base_url": "https://api.siliconflow.cn/v1",
                "api_key": "",
                "model": "BAAI/bge-reranker-v2-m3",
            },
        )
        config.setdefault("summary_api", {"base_url": "", "api_key": "", "model": ""})
        config.setdefault(
            "memory",
            {
                "recent_rounds": 10,
                "summarize_every": 16,
                "recall_n": 30,
                "top_k": 5,
                "recall_log": True,
            },
        )
        config.setdefault("auth", {"enabled": False, "password": ""})
        config.setdefault(
            "tts",
            {
                "enabled": False,
                "base_url": "https://api.minimax.chat/v1",
                "api_key": "",
                "group_id": "",
                "model": "speech-01-turbo",
                "voice_id": "female-tianmei",
                "speed": 1.0,
                "vol": 1.0,
                "pitch": 0,
                "format": "mp3",
                "sample_rate": 32000,
                "autoplay": True,
                "skip_narration": False,
            },
        )


def save_config():
    with config_lock:
        CONFIG_FILE.write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )


# ================= 局域网访问口令鉴权 =================
def _auth_cfg():
    return config.get("auth") or {}


def _auth_enabled():
    a = _auth_cfg()
    return bool(a.get("enabled") and a.get("password"))


def _auth_token_for(password):
    """口令 → 稳定 token（无状态，重启不失效）。token = sha256('chatbridge:'+口令)。"""
    return hashlib.sha256(
        ("chatbridge:" + (password or "")).encode("utf-8")
    ).hexdigest()


def _expected_token():
    return _auth_token_for(_auth_cfg().get("password", ""))
