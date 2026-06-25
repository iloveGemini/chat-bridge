# -*- coding: utf-8 -*-
"""全局路径与端口常量（只读，进程内不变）。"""
import sys
from pathlib import Path

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8800
ROOT = Path(__file__).resolve().parent.parent  # 项目根目录（core 的上一级）
DATA_DIR = ROOT / "data"
SESSIONS_DIR = DATA_DIR / "sessions"
PROMPTS_DIR = DATA_DIR / "prompts"
PRESETS_DIR = DATA_DIR / "prompts" / "_preset"
ARCHIVE_DIR = DATA_DIR / "archive"
UPLOAD_DIR = DATA_DIR / "uploads"
CONFIG_FILE = ROOT / "config.json"
MEMORY_DB = DATA_DIR / "memory.db"
JOBS_DB = DATA_DIR / "jobs.db"
TTS_DIR = DATA_DIR / "tts"
