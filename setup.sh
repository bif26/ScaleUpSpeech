#!/usr/bin/env bash
# LanguageShadow - setup script (idempotent: safe to re-run, skips finished work)
#
# Steps:
#   1. Check Python 3.10+ and ffmpeg
#   2. Create Python venv and install backend deps (only if missing)
#   3. Install Node.js deps for the Svelte UI and build it to ui/build/
#      (only if missing or stale)
#
# NOTE: The Whisper model download is NO LONGER part of setup.sh.
# It is a SEPARATE step that the user runs on their own schedule, because
# the model is large (150 MB – 3 GB depending on size) and a slow-internet
# user should not be blocked by it during initial setup.
#
#   After ./setup.sh finishes, run:
#       python3 download_model.py            # downloads the model
#       python3 download_model.py --status    # just checks if cached
#
# Or pass --with-model to this script to do the download inline (the old
# behaviour). Useful for headless / CI installs where you want everything
# ready in one shot.
#
# Re-running this script after everything is installed finishes in ~1 second.
# Day-to-day you do NOT need to run it at all — ./start_manager.sh will call
# it automatically only when something is actually missing.

set -e
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
VENV=".venv"
WITH_MODEL=0

for arg in "$@"; do
  case "$arg" in
    --with-model) WITH_MODEL=1 ;;
    --help|-h)
      echo "Usage: ./setup.sh [--with-model]"
      echo "  --with-model  Also run download_model.py at the end (old behaviour)."
      echo "                By default the model download is a separate step:"
      echo "                python3 download_model.py"
      exit 0
      ;;
  esac
done

echo "[1/4] Checking Python (need 3.10+)..."
"$PY" -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10+ required"' || {
  echo "ERROR: Python 3.10+ is required. On Arch:"
  echo "  sudo pacman -S python"
  exit 1
}

echo "[2/4] Checking ffmpeg + Node.js..."
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

# ---------------------------------------------------------------------------
# [3/4] Python venv + requirements — installed once, reused forever.
# ---------------------------------------------------------------------------
echo "[3/4] Python venv + backend requirements..."

if [ ! -x "$VENV/bin/python" ]; then
  echo "  Creating venv..."
  "$PY" -m venv "$VENV"
fi

# Shellcheck disable=SC1091
source "$VENV/bin/activate"

# All runtime imports the backend needs. If this succeeds, nothing to install.
if "$VENV/bin/python" -c "import fastapi, uvicorn, websockets, httpx, pydantic, \
faster_whisper, numpy, psutil, rapidfuzz, phonetics, huggingface_hub, tqdm" >/dev/null 2>&1; then
  echo "  Dependencies already installed — skipping pip install."
else
  echo "  Installing/upgrading requirements (first run or after requirements.txt changed)..."
  "$VENV/bin/pip" install --upgrade pip
  "$VENV/bin/pip" install -r requirements.txt
fi

# Quick sanity check: phonetics + ffmpeg really usable.
python - <<'PYEOF'
import phonetics
assert phonetics.metaphone("hello"), "phonetics test failed"
print("  phonetics OK")
import shutil
assert shutil.which("ffmpeg"), "ffmpeg not on PATH"
print("  ffmpeg OK")
PYEOF

# ---------------------------------------------------------------------------
# [4/4] Svelte UI — built only when missing or older than the sources.
# ---------------------------------------------------------------------------
echo "[4/4] Svelte UI (Catppuccin Mocha + shadcn-svelte)..."

needs_build() {
  # Called from inside ui/ (after `cd ui`). No build yet -> build. Otherwise
  # build only when some source file is newer than the last build output.
  [ ! -f build/index.html ] && return 0
  local newer
  newer=$(find src static package.json svelte.config.js vite.config.ts tsconfig.json \
                -type f -newer build/index.html 2>/dev/null | head -1)
  [ -n "$newer" ]
}

cd ui
if [ ! -d node_modules ]; then
  echo "  Installing npm dependencies (one-time)..."
  npm install --no-audit --no-fund
fi

# Some newer npm versions ship with an "install-scripts" allowlist that BLOCKS
# esbuild's postinstall ("npm warn install-scripts ... esbuild@x (postinstall:
# node install.js)"). When that happens, `vite build` later dies with
# "esbuild: Failed to install correctly". Detect and self-heal by running the
# installer directly — works no matter what the npm policy is.
if ! node -e "require('esbuild')" >/dev/null 2>&1; then
  echo "  esbuild binary missing (npm blocked its install script) — fixing..."
  node node_modules/esbuild/install.js >/dev/null 2>&1 \
    || npm rebuild esbuild --foreground-scripts \
    || npm install esbuild --force --no-audit --no-fund
  node -e "require('esbuild')" >/dev/null 2>&1 \
    && echo "  esbuild OK" \
    || { echo "ERROR: esbuild still broken. Run manually:  cd ui && node node_modules/esbuild/install.js"; exit 1; }
fi

if needs_build; then
  echo "  Building UI..."
  npm run build
else
  echo "  UI build is up to date — skipping."
fi
cd ..

# ---------------------------------------------------------------------------
# Whisper model — SEPARATE step. Just show status, do not download here
# unless the user explicitly asked for --with-model.
# ---------------------------------------------------------------------------
echo ""
echo "Whisper model status:"

MODEL_NAME_DEFAULT="small"
MODEL_FOR_CACHE="${LS_MODEL:-$MODEL_NAME_DEFAULT}"

if [ "$WITH_MODEL" = "1" ]; then
  echo "  --with-model given: downloading now..."
  python download_model.py || {
    echo ""
    echo "ERROR: model download failed. You can retry later with:"
    echo "  python3 download_model.py"
    echo "(setup is otherwise complete — only the model is missing.)"
    exit 1
  }
else
  # Just probe the cache. download_model.py --status exits 0 when cached.
  if python download_model.py --status >/dev/null 2>&1; then
    echo "  Model '$MODEL_FOR_CACHE' already cached — ready."
  else
    echo "  Model '$MODEL_FOR_CACHE' NOT cached yet."
    echo "  Run this when you have time (one-time, ~500 MB on slow internet):"
    echo "    python3 download_model.py"
    echo "  Until then, /api/assess-speech will auto-download on first request"
    echo "  (slower first-use) or fail offline."
  fi
fi

echo ""
echo "Setup complete. Next steps:"
echo "  1. (one-time) python3 download_model.py   # download the Whisper model"
echo "  2. (every day) ./start_manager.sh          # launch the manager"
echo "Then open http://127.0.0.1:8765/ in your browser."
echo ""
echo "To use your existing YouTube extension, point it at:"
echo "  POST http://127.0.0.1:8000/api/assess-speech"
echo "(CORS is enabled for all origins. Full contract in API.md.)"
