#!/usr/bin/env bash
# LanguageShadow - one-time setup script
#
# Steps:
#   1. Check Python 3.10+ and ffmpeg
#   2. Create Python venv and install backend deps
#   3. Pre-download the Whisper model
#   4. Install Node.js deps for the Svelte UI and build it to ui/build/

set -e
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
VENV=".venv"

echo "[1/5] Checking Python (need 3.10+)..."
"$PY" -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10+ required"' || {
  echo "ERROR: Python 3.10+ is required. On Arch:"
  echo "  sudo pacman -S python"
  exit 1
}

echo "[2/5] Checking ffmpeg + Node.js..."
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "WARNING: ffmpeg not found. faster-whisper needs it for webm/opus decoding."
  echo "  Arch:   sudo pacman -S ffmpeg"
  echo "  Debian: sudo apt install ffmpeg"
fi
if ! command -v node >/dev/null 2>&1; then
  echo "ERROR: Node.js not found. The Svelte UI build needs it."
  echo "  Arch:   sudo pacman -S nodejs npm"
  echo "  Debian: sudo apt install nodejs npm"
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "ERROR: npm not found. Install Node.js + npm first."
  exit 1
fi

echo "[3/5] Creating Python venv and installing backend requirements..."
if [ ! -d "$VENV" ]; then
  "$PY" -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
pip install --upgrade pip --quiet
pip install -r requirements.txt

echo "[4/5] Pre-warming whisper model..."
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

echo "[5/5] Building Svelte UI (Catppuccin Mocha + shadcn-svelte)..."
cd ui
if [ ! -d node_modules ]; then
  npm install
fi
npm run build
cd ..
echo "  Built to: $(pwd)/ui/build"

echo ""
echo "Setup complete. Next: ./start_manager.sh"
echo "Then open http://127.0.0.1:8765/ in your browser."
echo ""
echo "To use your existing YouTube extension, point it at:"
echo "  POST http://127.0.0.1:8000/api/assess-speech"
echo "(CORS is enabled for all origins. Full contract in API.md.)"
