# -*- coding: utf-8 -*-
"""语音合成（MiniMax 预置音色 T2A 旁路）：把回复正文合成语音，挂到消息上。"""
import base64
import hashlib
import json
import re
import urllib.request

from core.config import config
from core.paths import TTS_DIR, PROMPTS_DIR
from core.net import log_print, _safe_name


def _tts_cfg():
    return config.get("tts", {}) or {}


def _strip_narration(text):
    """只读台词：去掉旁白——成对（中/英）括号、【】、*…* 包裹，以及整行的 『…』场景/时间头。
    保留 <#x#> 停顿标记不动。去完为空说明本条没台词。"""
    t = text or ""
    t = re.sub(r"（[^）]*）", "", t)  # 全角括号旁白
    t = re.sub(r"\([^)]*\)", "", t)  # 半角括号旁白
    t = re.sub(r"【[^】]*】", "", t)  # 方头括号旁白
    t = re.sub(r"\*[^*\n]+\*", "", t)  # *星号* 旁白
    t = re.sub(r"(?m)^\s*『[^』]*』\s*$", "", t)  # 整行场景/时间头
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\n{2,}", "\n", t)
    return t.strip()


def synth_tts(text, override=None):
    """把一段回复正文合成为语音，返回可供前端播放的相对 URL（/data/tts/xxx.mp3）；
    未开启 / 缺 key / 失败一律返回 None，绝不影响文字消息。
    走 MiniMax T2A v2 协议，base_url/api_key/group_id 全 config 可填。
    override：每角色/试听传入的音色覆盖（voice_id/speed/pitch/vol/model），缺省回落 config.tts。"""
    cfg = _tts_cfg()
    if not cfg.get("enabled"):
        return None
    base = (cfg.get("base_url") or "").strip().rstrip("/")
    key = (cfg.get("api_key") or "").strip()
    if not base or not key:
        return None

    # 去掉排版/旁白用的标签残留，但保留 MiniMax 停顿标记 <#x#>（不以 < 后紧跟 # 的不删）
    speak = re.sub(r"<(?!#)[^>]*?>", "", (text or "")).strip()
    # 只读台词：去掉括号旁白后再合成；去完为空说明本条没台词，直接不出声
    if cfg.get("skip_narration"):
        speak = _strip_narration(speak)
    if not speak:
        return None
    if len(speak) > 800:  # 过长截断，避免合成超时/超额
        speak = speak[:800]

    # 角色音色覆盖优先，缺项回落到全局 config.tts
    ov = override or {}

    def _pick(k, d):
        v = ov.get(k)
        return v if v not in (None, "") else cfg.get(k, d)

    voice_id = _pick("voice_id", "female-tianmei")
    model = _pick("model", "speech-01-turbo")
    fmt = str(_pick("format", "mp3")).lower()
    speed = float(_pick("speed", 1.0))
    vol = float(_pick("vol", 1.0))
    pitch = int(_pick("pitch", 0))
    sample_rate = int(_pick("sample_rate", 32000))
    emotion = str(
        _pick("emotion", "") or ""
    ).strip()  # 每条消息的情绪（happy/sad/...），空则不传

    # 缓存键含全部影响音频的参数，避免换音色/语速/情绪后还命中旧文件
    h = hashlib.md5(
        f"{model}|{voice_id}|{speed}|{vol}|{pitch}|{emotion}|{speak}".encode("utf-8")
    ).hexdigest()
    TTS_DIR.mkdir(parents=True, exist_ok=True)
    out_file = TTS_DIR / f"{h}.{fmt}"
    rel_url = f"/data/tts/{out_file.name}"
    if out_file.exists() and out_file.stat().st_size > 0:
        return rel_url

    url = base + "/t2a_v2"
    gid = (cfg.get("group_id") or "").strip()
    if gid:
        url += f"?GroupId={gid}"
    payload = {
        "model": model,
        "text": speak,
        "stream": False,
        "voice_setting": {
            "voice_id": voice_id,
            "speed": speed,
            "vol": vol,
            "pitch": pitch,
            **({"emotion": emotion} if emotion else {}),
        },
        "audio_setting": {
            "sample_rate": sample_rate,
            "bitrate": 128000,
            "format": fmt,
            "channel": 1,
        },
    }
    try:
        rq = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            },
        )
        with urllib.request.urlopen(rq, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log_print(f"🔇 [TTS] 请求失败: {e}")
        return None

    # MiniMax 标准返回：data.audio = hex 编码音频字节
    data = (result.get("data") or {}) if isinstance(result, dict) else {}
    audio_hex = data.get("audio")
    if not audio_hex:
        log_print(
            f"🔇 [TTS] 无音频返回: base_resp={result.get('base_resp') if isinstance(result, dict) else result}"
        )
        return None
    try:
        audio_bytes = bytes.fromhex(audio_hex)
    except ValueError:
        try:  # 个别代理返回 base64 而非 hex，兜底再试一次
            audio_bytes = base64.b64decode(audio_hex)
        except Exception:
            log_print("🔇 [TTS] 音频解码失败（既非 hex 也非 base64）")
            return None
    out_file.write_bytes(audio_bytes)
    log_print(f"🔊 [TTS] 合成成功 {voice_id} {len(audio_bytes)}B -> {rel_url}")
    return rel_url


def _attach_tts(msg):
    """给一条 assistant 消息就地挂上 audio 字段；失败静默跳过，文字照常。"""
    try:
        u = synth_tts(msg.get("text", ""))
        if u:
            msg["audio"] = u
    except Exception as e:
        log_print(f"🔇 [TTS] 挂载异常: {e}")
    return msg


def _character_voice(char_name):
    """读某角色 prompt json 里的 voice 覆盖（{voice_id, speed, pitch, ...}）；没有就 {}。"""
    try:
        fp = PROMPTS_DIR / "character" / f"{_safe_name(char_name)}.json"
        if fp.exists():
            d = json.loads(fp.read_text(encoding="utf-8"))
            v = d.get("voice")
            if isinstance(v, dict):
                return v
    except Exception:
        pass
    return {}
