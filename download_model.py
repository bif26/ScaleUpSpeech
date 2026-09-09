#!/usr/bin/env python
"""
Download the Whisper model ONCE, with visible progress.

Why this script exists
----------------------
faster-whisper downloads its model through huggingface_hub, and in some
setups (xet backend, progress bars disabled, output piped) the download is
completely silent - on a slow connection it looks like setup hung forever.
This script makes the download explicit and bounded:

  * shows the model name, repo id, cache location and per-file sizes first
  * streams every file with a normal huggingface_hub progress bar
  * probes the local cache FIRST - if complete, downloads 0 MB
  * guarantees "download once": the cache lives OUTSIDE the repo in
    ~/.cache/huggingface/hub (or $HF_HOME), so `git pull`, `rm -rf .venv`
    and even a fresh re-clone never trigger a re-download
  * finishes by loading the model straight from disk (zero network) to
    prove the install works before setup continues

Usage:
    python download_model.py            # uses LS_MODEL / config.py (default: small)
    python download_model.py tiny       # override model just for this run
"""

from __future__ import annotations

import os
import sys

# Force progress bars ON before huggingface_hub is imported: some machines
# export HF_HUB_DISABLE_PROGRESS_BARS=1 globally, which makes the download
# invisible. "0" explicitly means "do not disable".
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "0"

import fnmatch  # noqa: E402
from pathlib import Path  # noqa: E402

import config  # noqa: E402  (repo shared config - resolves LS_MODEL etc.)

from tqdm.auto import tqdm  # noqa: E402

# EXACTLY the file set faster-whisper's own download_model() fetches — so the
# cache this script fills is byte-for-byte the cache WhisperModel() expects.
# (faster-whisper downloads these with tqdm_class=disabled_tqdm, i.e. fully
# silent — that is why the download was invisible before this script existed.)
ALLOW_PATTERNS = [
    "config.json",
    "preprocessor_config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.*",
]


def _repo_id_for(model_name: str) -> str:
    """Map a faster-whisper model name to its HuggingFace repo id."""
    try:
        from faster_whisper.utils import _MODELS  # private but stable

        if model_name in _MODELS:
            return _MODELS[model_name]
    except Exception:
        pass
    return f"Systran/faster-whisper-{model_name}"


def _human(num_bytes: float) -> str:
    mb = num_bytes / 1e6
    return f"{mb:.0f} MB" if mb >= 1 else f"{num_bytes / 1e3:.0f} KB"


def _enable_progress_bars() -> None:
    try:
        from huggingface_hub.utils import enable_progress_bars

        enable_progress_bars()
    except Exception:
        pass  # older hub versions: bars are on by default anyway


class VerboseTqdm(tqdm):
    """tqdm that additionally prints a plain progress line every ~5%.

    Interactive bars can vanish when output is piped/redirected or when a
    backend (xet) renders oddly; the plain lines always survive, so on a
    slow connection you ALWAYS see the download moving.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._last_mark = 0

    def update(self, n=1):
        super().update(n)
        try:
            total = self.total
            if total and total > 1e6:  # plain lines only for files > 1 MB
                done = int(self.n)
                pct = int(done * 100 / total)
                if pct >= self._last_mark + 5 or pct >= 100:
                    self._last_mark = pct
                    name = (self.desc or "download").strip()
                    print(f"    {name}: {done / 1e6:.0f}/{total / 1e6:.0f} MB "
                          f"({pct}%)", flush=True)
        except Exception:
            pass


def _cache_hit(repo_id: str) -> str | None:
    """Return the local snapshot path if the model is fully cached, else None.

    hub symlinks snapshots/<sha>/model.bin into blobs/ only AFTER the file
    finished downloading, so an interrupted download leaves no symlink.
    Checking for model.bin is what makes this a *complete*-cache probe.
    """
    from huggingface_hub import snapshot_download

    try:
        cached = snapshot_download(
            repo_id=repo_id, allow_patterns=ALLOW_PATTERNS, local_files_only=True
        )
    except Exception:
        return None
    if any(Path(cached).glob("model.bin")):
        return cached
    return None


def _show_sizes(repo_id: str) -> None:
    """Print per-file sizes so the user knows what is coming and how long."""
    try:
        from huggingface_hub import HfApi

        info = HfApi().model_info(repo_id, files_metadata=True)
        wanted = [
            s
            for s in info.siblings
            if any(fnmatch.fnmatch(s.rfilename, p) for p in ALLOW_PATTERNS)
            and s.size
        ]
        for s in sorted(wanted, key=lambda x: -x.size):
            print(f"    {s.rfilename:<30} {_human(s.size)}")
        print(f"  Total     : ~{_human(sum(s.size for s in wanted))} "
              "(ONE-TIME download - every later run reuses the cache)")
    except Exception:
        print("  (could not fetch file sizes - downloading anyway)")


def _load_from_disk(model_path: str) -> None:
    """Load the model purely from disk (zero network) to prove it works.

    This is also where the old setup crashed: ctranslate2 rejects
    cpu_threads=None. We always pass the int (0 = auto / all cores).
    """
    print("  Loading model from cache (no network)...")
    from faster_whisper import WhisperModel

    model = WhisperModel(
        model_path,
        device=config.MODEL_DEVICE,
        compute_type=config.MODEL_COMPUTE_TYPE,
        cpu_threads=int(config.MODEL_CPU_THREADS),  # int only - 0 = auto
    )
    del model  # verification load only; the worker loads it again at runtime
    print("  Model ready.")


def main() -> int:
    model_name = sys.argv[1] if len(sys.argv) > 1 else config.MODEL_NAME
    repo_id = _repo_id_for(model_name)
    hf_home = os.environ.get("HF_HOME", "~/.cache/huggingface")

    print(f"  Model     : {model_name}")
    print(f"  Repo      : {repo_id}")
    print(f"  Cache dir : {hf_home}/hub  (outside this repo -> never re-downloaded)")

    _enable_progress_bars()

    # 1) Fast path: already fully cached -> 0 MB, no network at all.
    hit = _cache_hit(repo_id)
    if hit:
        print("  Already cached - skipping download entirely (0 MB).")
        print(f"  Cached at : {hit}")
        _load_from_disk(hit)
        return 0

    # 2) Not cached: show sizes, then download with visible progress bars.
    print("  Not cached yet - downloading now (one-time):")
    _show_sizes(repo_id)
    print("  Downloading (progress bars below)...")
    from huggingface_hub import snapshot_download

    snapshot_dir = snapshot_download(
        repo_id=repo_id, allow_patterns=ALLOW_PATTERNS, tqdm_class=VerboseTqdm
    )
    print(f"  Download complete: {snapshot_dir}")

    # 3) Verify it loads from disk (also catches broken installs early).
    _load_from_disk(snapshot_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
