#!/usr/bin/env bash
# Start the LanguageShadow manager in the background.
# The manager stays on forever (it's tiny - ~30-50 MB).
# It will spawn the worker on demand when the user starts speaking.
#
# This is your EVERYDAY command. If the venv or dependencies are missing it
# runs ./setup.sh once (which itself skips anything already done); once
# installed, starting is instant — no downloads, no reinstalls.
#
# The Whisper model download is intentionally NOT part of setup.sh. We probe
# the cache here and warn loudly if the model is missing, because the worker
# cannot transcribe without it (and silently auto-downloading on first use
# is exactly the slow-internet trap the user is trying to avoid).

cd "$(dirname "$0")"
VENV=".venv"
PY="$VENV/bin/python"

# Self-heal: only run setup when something is actually missing.
if [ ! -x "$PY" ] || ! "$PY" -c "import fastapi, uvicorn, websockets, httpx, \
pydantic, faster_whisper, numpy, psutil, rapidfuzz, phonetics" >/dev/null 2>&1; then
  echo "First run (or dependencies missing) — running ./setup.sh once..."
  ./setup.sh
fi

# Probe the Whisper model cache. The worker can technically auto-download on
# first request, but that is exactly the silent, slow, looks-like-it-hung
# behaviour we are trying to avoid. Warn explicitly so the user knows what
# to do.
echo "Checking Whisper model cache..."
if "$PY" download_model.py --status >/dev/null 2>&1; then
  echo "  Model is cached — worker will start offline."
else
  echo "  ┌──────────────────────────────────────────────────────────────────┐"
  echo "  │ WARNING: Whisper model is NOT cached yet.                       │"
  echo "  │                                                                  │"
  echo "  │ The worker cannot transcribe without it. On first use it would   │"
  echo "  │ silently download ~500 MB (or more), which on a slow connection  │"
  echo "  │ looks like the app is frozen.                                    │"
  echo "  │                                                                  │"
  echo "  │ Run this ONE-TIME command now, when you have time:               │"
  echo "  │     python3 download_model.py                                    │"
  echo "  │                                                                  │"
  echo "  │ It shows progress bars + per-file sizes and only ever runs once   │"
  echo "  │ (the cache lives in ~/.cache/huggingface, survives git pull and  │"
  echo "  │ even a fresh re-clone).                                          │"
  echo "  └──────────────────────────────────────────────────────────────────┘"
fi

mkdir -p logs

# Kill any previous manager.
if [ -f logs/manager.pid ]; then
  OLD=$(cat logs/manager.pid)
  if kill -0 "$OLD" 2>/dev/null; then
    echo "Stopping previous manager (pid $OLD)..."
    kill "$OLD" 2>/dev/null || true
    sleep 1
  fi
fi

# Start the manager detached.
nohup "$PY" manager.py > logs/manager.out 2>&1 &
echo $! > logs/manager.pid
sleep 1

if kill -0 "$(cat logs/manager.pid)" 2>/dev/null; then
  echo "Manager started (pid $(cat logs/manager.pid)) on http://127.0.0.1:8765/"
  echo "Worker will spawn automatically when you start speaking."
  # Only prompt when attached to a terminal (never inside systemd/scripts).
  if [ -t 0 ]; then
    echo "Auto-start worker now? [y/N]"
    read -r ans
    if [ "$ans" = "y" ] || [ "$ans" = "Y" ]; then
      curl -s -X POST http://127.0.0.1:8765/manager/start >/dev/null && echo "Worker starting..."
    fi
  fi
else
  echo "ERROR: manager did not start. Check logs/manager.out"
  exit 1
fi
