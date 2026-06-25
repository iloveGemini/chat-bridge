import os
from pathlib import Path

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "writer.md"
SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8")
