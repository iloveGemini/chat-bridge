import os
import sys
from functools import lru_cache

# 解决命名冲突：删除根目录下的旧 tiktoken.py
_old_tiktoken = os.path.join(os.path.dirname(__file__), "tiktoken.py")
if os.path.exists(_old_tiktoken):
    try:
        os.remove(_old_tiktoken)
    except Exception:
        pass
    if "tiktoken" in sys.modules and getattr(sys.modules["tiktoken"], "__file__", "") == _old_tiktoken:
        del sys.modules["tiktoken"]

try:
    import tiktoken
except ImportError:
    tiktoken = None

@lru_cache(maxsize=1)
def get_encoder():
    if tiktoken is None:
        raise ImportError("tiktoken is not installed")
    return tiktoken.get_encoding("cl100k_base")

def count_tokens_exact(text: str) -> int:
    """精准计算 Token 数量"""
    if not text:
        return 0
    try:
        encoder = get_encoder()
        return len(encoder.encode(text))
    except Exception:
        return len(text) // 3
