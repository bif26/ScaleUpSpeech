#!/usr/bin/env bash
# Stop both the manager and the worker (and any frozen worker).
cd "$(dirname "$0")"

# Ask the manager to stop the worker gracefully.
if curl -s -X POST http://127.0.0.1:8765/manager/stop >/dev/null 2>&1; then
  echo "Worker stopped via manager."
fi

# Kill the manager itself.
if [ -f logs/manager.pid ]; then
  PID=$(cat logs/manager.pid)
  if kill -0 "$PID" 2>/dev/null; then
    echo "Stopping manager (pid $PID)..."
    kill "$PID" 2>/dev/null || true
    sleep 1
    kill -9 "$PID" 2>/dev/null || true
  fi
  rm -f logs/manager.pid
fi

# Belt-and-braces: any stray worker.py processes started by us.
pkill -f "$(pwd)/worker.py" 2>/dev/null || true

echo "LanguageShadow fully stopped."
