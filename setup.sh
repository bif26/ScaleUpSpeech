#!/usr/bin/env bash
# LanguageShadow - setup script (idempotent: safe to re-run, skips finished work)
#
# Steps:
#   1. Check Python 3.10+ and ffmpeg
#   2. Create Python venv and install backend deps (only if missing)
#   3. Pre-download the Whisper model (only if not cached)
#   4. Install Node.js deps for the Svelte UI and build it to ui/build/
#      (only if missing or stale)
#
# Re-running this script after everything is installed finishes in ~1 second.
# Day-to-day you do NOT need to run it at all — ./start_manager.sh will call
# it automatically only when something is actually missing.

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

# ---------------------------------------------------------------------------
# [3/5] Python venv + requirements — installed once, reused forever.
# ---------------------------------------------------------------------------
echo "[3/5] Python venv + backend requirements..."

if [ ! -x "$VENV/bin/python" ]; then
  echo "  Creating venv..."
  "$PY" -m venv "$VENV"
fi

# Shellcheck disable=SC1091
source "$VENV/bin/activate"

# All runtime imports the backend needs. If this succeeds, nothing to install.
if "$VENV/bin/python" -c "import fastapi, uvicorn, websockets, httpx, pydantic, \
faster_whisper, numpy, psutil, rapidfuzz, phonetics" >/dev/null 2>&1; then
  echo "  Dependencies already installed — skipping pip install."
else
  echo "  Installing/upgrading requirements (first run or after requirements.txt changed)..."
  "$VENV/bin/pip" install --upgrade pip
  "$VENV/bin/pip" install -r requirements.txt
fi

# ---------------------------------------------------------------------------
# [4/5] Whisper model — downloaded ONCE into the HF cache, then reused forever.
# ---------------------------------------------------------------------------
echo "[4/5] Whisper model check..."
MODEL_NAME_DEFAULT="small"
# config.py honours LS_MODEL; mirror it so the cache path matches.
MODEL_FOR_CACHE="${LS_MODEL:-$MODEL_NAME_DEFAULT}"
HF_HUB_DIR="${HF_HOME:-$HOME/.cache/huggingface}/hub"
MODEL_CACHE=$(ls -d "$HF_HUB_DIR"/models--*faster-whisper-"$MODEL_FOR_CACHE"* 2>/dev/null | head -1 || true)

# "Cached" only counts when the big model.bin blob is actually there: hub
# symlinks snapshots/<sha>/model.bin into blobs/ only AFTER the download
# finished, so an interrupted download leaves no symlink and we correctly
# resume instead of pretending everything is fine.
if [ -n "$MODEL_CACHE" ] && ls "$MODEL_CACHE"/snapshots/*/model.bin >/dev/null 2>&1; then
  echo "  Model '$MODEL_FOR_CACHE' already cached — skipping download (0 MB)."
  echo "    $MODEL_CACHE"
else
  # One-time download with VISIBLE progress bars + size info, then a
  # no-network load to verify the install. Never re-downloads: the cache
  # lives in ~/.cache/huggingface, outside the repo (survives git pull,
  # rm -rf .venv, even a fresh re-clone).
  python download_model.py
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
# [5/5] Svelte UI — built only when missing or older than the sources.
# ---------------------------------------------------------------------------
echo "[5/5] Svelte UI (Catppuccin Mocha + shadcn-svelte)..."

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

echo ""
echo "Setup complete. Start everything with:  ./start_manager.sh"
echo "Then open http://127.0.0.1:8765/ in your browser."
echo ""
echo "To use your existing YouTube extension, point it at:"
echo "  POST http://127.0.0.1:8000/api/assess-speech"
echo "(CORS is enabled for all origins. Full contract in API.md.)"
