"""
Self-healing helpers for the local Hugging Face model cache.

Why this module exists
----------------------
The worker once failed to start with:

    RuntimeError: [json.exception.parse_error.101] parse error at line 1,
    column 1: syntax error while parsing value - unexpected end of input;
    expected '[', '{', or a literal

raised from inside ``ctranslate2.models.Whisper()``. The cause was a
**0-byte ``vocabulary.json``** inside the local HF snapshot directory.
That file does not even exist in the upstream ``Systran/faster-whisper-*``
repos (they ship ``vocabulary.txt``); an interrupted or manual "repair"
download had left an empty placeholder behind.

CTranslate2 prefers ``vocabulary.json`` over ``vocabulary.txt`` when it
finds one, so it tried to parse an empty file and the whole model load
failed - even though the cache was otherwise complete and a valid
``vocabulary.txt`` sat right next to the phantom.

``huggingface_hub`` never repairs this by itself: the broken files are not
in the upstream file list, so every re-download simply skips them forever.

The helpers here find and remove such corrupt leftovers, and can purge a
model's whole cache folder as a last resort before a clean re-download.
Only the standard library is imported so ``download_model.py`` can use
this module before its own dependency bootstrap runs.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import List


def hf_cache_root() -> Path:
    """Root of the HF hub cache, honoring HF_HUB_CACHE / HF_HOME like the hub."""
    env_cache = os.environ.get("HF_HUB_CACHE")
    if env_cache:
        return Path(env_cache).expanduser()
    env_home = os.environ.get("HF_HOME")
    if env_home:
        return Path(env_home).expanduser() / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def repo_id_for(model_name: str) -> str:
    """Map a model name ('small', 'large-v3', 'Systran/faster-whisper-small').

    Returns the Hugging Face repo id, or the unchanged input when it is a
    local directory path (callers check for that separately).
    """
    name = str(model_name).strip().rstrip("/")
    if not name or os.path.isdir(name):
        return name
    if "/" in name:
        return name
    try:
        # faster_whisper's private table is the source of truth when installed.
        from faster_whisper.utils import _MODELS  # type: ignore[attr-defined]

        if name in _MODELS:
            return _MODELS[name]
    except Exception:
        pass
    return f"Systran/faster-whisper-{name}"


def snapshot_dirs(model_name: str) -> List[Path]:
    """All local snapshot directories holding files for this model.

    Returns [] when the model is a local directory or nothing is cached.
    """
    name = str(model_name).strip().rstrip("/")
    if not name or os.path.isdir(name):
        return []  # explicit local dir: never touch the shared cache
    repo = repo_id_for(name)
    if "/" not in repo:
        return []
    snapshots = hf_cache_root() / ("models--" + repo.replace("/", "--")) / "snapshots"
    if not snapshots.is_dir():
        return []
    return sorted(p for p in snapshots.iterdir() if p.is_dir())


def sanitize_model_cache(model_name: str) -> List[str]:
    """Delete corrupt 0-byte regular files from the model's cached snapshots.

    A converted whisper CT2 model never contains a 0-byte file, so any
    empty regular file in a snapshot is a corrupt leftover (phantom) that
    can crash CTranslate2 or the tokenizer with a JSON parse error. Only
    regular files are removed - symlinks and directories are never touched
    here (use :func:`purge_model_cache` for a deeper reset).

    Returns the list of removed file paths.
    """
    removed: List[str] = []
    for snap in snapshot_dirs(model_name):
        for f in snap.rglob("*"):
            try:
                if f.is_symlink() or not f.is_file():
                    continue
                if f.stat().st_size == 0:
                    f.unlink()
                    removed.append(str(f))
            except OSError:
                # Unreadable/undeletable entry: skip it, the caller's retry
                # (or the purge path) will surface the real error if needed.
                continue
    return removed


def purge_model_cache(model_name: str) -> bool:
    """Delete the model's whole HF cache folder (last resort).

    Returns True when something was actually removed. Never touches a
    model given as a local directory path.
    """
    name = str(model_name).strip().rstrip("/")
    if not name or os.path.isdir(name):
        return False
    repo = repo_id_for(name)
    if "/" not in repo:
        return False
    folder = hf_cache_root() / ("models--" + repo.replace("/", "--"))
    if folder.is_dir():
        shutil.rmtree(folder, ignore_errors=True)
        return not folder.exists()
    return False
