"""
LanguageShadow - Shared configuration

All tunables live here so manager.py and worker.py share the same values
without one needing to import from the other (they are separate processes).
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
LANG_DIR = BASE_DIR / "languages"
STATIC_DIR = BASE_DIR / "static"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------
# Manager (port 1): lightweight, always-on, serves UI + REST API + WS proxy.
MANAGER_HOST = "127.0.0.1"
MANAGER_PORT = 8765

# Worker (port 2): heavy AI process, spawned on demand, auto-killed when idle.
WORKER_HOST = "127.0.0.1"
WORKER_PORT = 8000

# ---------------------------------------------------------------------------
# RAM budget
# ---------------------------------------------------------------------------
# Target ceiling for the whole LanguageShadow stack on top of Arch + browser.
# Manager is the only always-on process and is sized to stay well under this.
RAM_BUDGET_MB = 2300

# If worker RSS exceeds this for too long, manager will restart it.
WORKER_RAM_HARD_LIMIT_MB = 1600

# How often (seconds) manager samples worker RAM.
WORKER_RAM_SAMPLE_INTERVAL = 5

# ---------------------------------------------------------------------------
# Worker lifecycle
# ---------------------------------------------------------------------------
# Worker is auto-killed after this many seconds of no requests, freeing RAM.
WORKER_IDLE_TIMEOUT = int(os.environ.get("LS_IDLE_TIMEOUT", "60"))

# When manager is told to "freeze", worker is suspended (SIGSTOP) instead of
# killed, so it can be thawed instantly. RAM is NOT freed while frozen.
WORKER_FREEZE_INSTEAD_OF_KILL = os.environ.get("LS_FREEZE", "0") == "1"

# Heartbeat interval (worker -> manager).
WORKER_HEARTBEAT_INTERVAL = 2.0

# Heartbeat miss threshold before manager declares worker dead.
WORKER_HEARTBEAT_MISS = 3

# ---------------------------------------------------------------------------
# Whisper model
# ---------------------------------------------------------------------------
# Pick the smallest model that gives acceptable quality for your hardware.
#   tiny     ~150 MB   low quality, very fast
#   base     ~300 MB   ok for short phrases
#   small    ~500 MB   good balance (default)
#   medium   ~1.5 GB   better, slow on CPU
#   large-v3 >3 GB     too heavy for 8 GB RAM machines
MODEL_NAME = os.environ.get("LS_MODEL", "small")

# CPU int8 keeps RAM ~3x lower than float16 with minor quality loss.
MODEL_COMPUTE_TYPE = os.environ.get("LS_COMPUTE", "int8")

# Use CPU only (no CUDA) for portability. Set LS_DEVICE=cuda to enable GPU.
MODEL_DEVICE = os.environ.get("LS_DEVICE", "cpu")

# Number of CPU threads whisper can use. 0 = auto (all cores).
MODEL_CPU_THREADS = int(os.environ.get("LS_THREADS", "0"))

# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------
# Web client sends 16 kHz mono PCM float32 chunks over WebSocket.
SAMPLE_RATE = 16000
CHUNK_MS = 250  # streaming chunk length in milliseconds
CHUNK_SAMPLES = SAMPLE_RATE * CHUNK_MS // 1000  # 4000 samples per chunk

# VAD (voice activity detection) on the worker side, using faster-whisper's
# built-in Silero VAD. Skips silence, lowers CPU and RAM churn.
VAD_ENABLED = True
VAD_THRESHOLD = 0.35  # 0..1, higher = stricter

# ---------------------------------------------------------------------------
# Pronunciation scoring
# ---------------------------------------------------------------------------
# How strict word matching is.
#   0.85  very lenient (slight mispronunciation accepted)
#   0.75  lenient (default, good for learners)
#   0.65  strict (good for advanced learners)
#   0.55  very strict (near-native required)
WORD_MATCH_SIMILARITY = 0.75

# Minimum confidence (0..1) whisper must have in a word before scoring it.
MIN_WORD_CONFIDENCE = 0.30

# WPM target range used by the fluency scorer.
WPM_IDEAL_MIN = 100
WPM_IDEAL_MAX = 180

# ---------------------------------------------------------------------------
# Default language
# ---------------------------------------------------------------------------
DEFAULT_LANGUAGE = os.environ.get("LS_LANG", "en")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL = os.environ.get("LS_LOG", "INFO").upper()
LOG_FILE = LOG_DIR / "languageshadow.log"
