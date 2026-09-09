#!/usr/bin/env bash
# Start the LanguageShadow manager in the background.
# The manager stays on forever (it's tiny - ~30-50 MB).
# It will spawn the worker on demand when the user starts speaking.

cd "$(dirname "$0")"
VENV=".venv"
PY="$VENV/bin/python"

if [ ! -x "$PY" ]; then
  echo "Run ./setup.sh first."
  exit 1
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
  echo "Auto-start worker now? [y/N]"
  read -r ans
  if [ "$ans" = "y" ] || [ "$ans" = "Y" ]; then
    curl -s -X POST http://127.0.0.1:8765/manager/start >/dev/null && echo "Worker starting..."
  fi
else
  echo "ERROR: manager did not start. Check logs/manager.out"
  exit 1
fi
