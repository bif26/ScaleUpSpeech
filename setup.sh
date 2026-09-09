#!/usr/bin/env bash
# LanguageShadow - one-time setup script
# Creates a virtualenv, installs requirements, and pre-downloads the whisper
# model so the first user request is fast.

set -e
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
VENV=".venv"

echo "[1/4] Checking Python (need 3.10+)..."
"$PY" -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10+ required"' || {
  echo "ERROR: Python 3.10+ is required. On Arch:"
  echo "  sudo pacman -S python"
  exit 1
}

echo "[2/4] Checking ffmpeg..."
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "WARNING: ffmpeg not found. faster-whisper needs it for some audio formats."
  echo "  Arch:   sudo pacman -S ffmpeg"
  echo "  Debian: sudo apt install ffmpeg"
fi

echo "[3/4] Creating venv and installing requirements..."
if [ ! -d "$VENV" ]; then
  "$PY" -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
pip install --upgrade pip --quiet
pip install -r requirements.txt

echo "[4/4] Pre-warming whisper model '$(grep MODEL_NAME config.py | head -1)'..."
# Run a tiny import so faster-whisper downloads the model now AND verify
# phonetics is available (we need it for pronunciation scoring).
python - <<'PYEOF'
import config
print("Pre-loading model:", config.MODEL_NAME)
from faster_whisper import WhisperModel
m = WhisperModel(config.MODEL_NAME, device=config.MODEL_DEVICE,
                 compute_type=config.MODEL_COMPUTE_TYPE,
                 cpu_threads=config.MODEL_CPU_THREADS or None)
print("Model ready.")
# Verify phonetics + ffmpeg are available.
import phonetics
assert phonetics.metaphone("hello"), "phonetics test failed"
print("phonetics OK")
import shutil
assert shutil.which("ffmpeg"), "ffmpeg not on PATH"
print("ffmpeg OK")
PYEOF

echo ""
echo "Setup complete. Next: ./start_manager.sh"
echo "Then open http://127.0.0.1:8765/ in your browser."
echo ""
echo "To use your existing YouTube extension, point it at:"
echo "  POST http://127.0.0.1:8000/api/assess-speech"
echo "(CORS is enabled for all origins.)"
