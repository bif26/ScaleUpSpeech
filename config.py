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
# The Svelte UI builds to ui/build/. setup.sh runs `npm run build` for you.
# If you prefer to run the UI in dev mode (vite), set LS_UI_DEV=1 and the
# manager will proxy / to http://127.0.0.1:5173 — useful for hot reload.
UI_BUILD_DIR = BASE_DIR / "ui" / "build"
STATIC_DIR = UI_BUILD_DIR
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
# Default raised 60 -> 300: killing after 60 s felt instant to users and made
# every pause cost a full model reload. With busy-tracking (active WS sessions
# block the kill entirely, see manager.py) 300 s is a safe, friendlier default.
WORKER_IDLE_TIMEOUT = int(os.environ.get("LS_IDLE_TIMEOUT", "300"))

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

# Silero VAD fine-tuning for STREAMING (faster-whisper VadOptions).
# The library defaults (min_silence 2000 ms, speech_pad 400 ms) are tuned
# for batch files; on a rolling buffer they keep segments open too long,
# which delays settling and re-transcribes the same tail over and over.
VAD_MIN_SPEECH_MS = int(os.environ.get("LS_VAD_MIN_SPEECH_MS", "150"))
VAD_MIN_SILENCE_MS = int(os.environ.get("LS_VAD_MIN_SILENCE_MS", "600"))
VAD_SPEECH_PAD_MS = int(os.environ.get("LS_VAD_SPEECH_PAD_MS", "120"))

# Streaming pacing (worker side, StreamingSession.append). whisper-small on
# CPU transcribes SLOWER than real time (~3 s wall for 1.7 s of speech, see
# languageshadow.log 2026-09-10), so the worker waits until at least `gap`
# seconds of NEW audio have accumulated since the last whisper call, where
# gap adapts to the measured call time (1.5x, clamped to the bounds below).
# Without pacing, one transcription per incoming chunk makes the socket
# backlog grow without bound: partials never reach the browser and the
# final result is never computed.
STREAM_MIN_GAP_S = float(os.environ.get("LS_STREAM_MIN_GAP", "1.0"))
STREAM_MAX_GAP_S = float(os.environ.get("LS_STREAM_MAX_GAP", "6.0"))

# Beam size. 1 (greedy) is ~2x faster on CPU with near-identical text for
# clear speech; set LS_BEAM=5 for maximum accuracy on a fast machine.
BEAM_SIZE = int(os.environ.get("LS_BEAM", "1"))

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
