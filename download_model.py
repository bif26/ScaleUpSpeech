#!/usr/bin/env python3
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

Self-bootstrap
--------------
This script is the ONLY piece of the project that needs huggingface_hub +
tqdm. They are not imported anywhere else in the codebase, so we install
them on demand here (into the same interpreter that runs the script) instead
of forcing them into requirements.txt for every user. That way:

  python3 download_model.py            # works on a bare system Python too
  .venv/bin/python download_model.py   # works inside the project venv

Separated from ./setup.sh
-------------------------
setup.sh no longer runs this script automatically. Users with slow internet
should run it manually ONCE after `./setup.sh` and before `./start_manager.sh`:

    ./setup.sh                 # installs python + node deps (no model)
    python3 download_model.py  # <-- separate step, run when you have time
    ./start_manager.sh         # starts the manager; worker works offline

Usage:
    python3 download_model.py            # uses LS_MODEL / config.py (default: small)
    python3 download_model.py tiny       # override model just for this run
    python3 download_model.py --status   # only check if cached, never download
    python3 download_model.py --check    # alias for --status
    python3 download_model.py --repair   # remove corrupt 0-byte cache leftovers,
                                         # then verify the model loads (no download
                                         # unless files are really missing)
"""

from __future__ import annotations

import os
import sys
import subprocess

# Force progress bars ON before huggingface_hub is imported: some machines
# export HF_HUB_DISABLE_PROGRESS_BARS=1 globally, which makes the download
# invisible. "0" explicitly means "do not disable".
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "0"

import fnmatch  # noqa: E402
from pathlib import Path  # noqa: E402

# Stdlib-only cache repair helpers (same dir as this script). Importing here
# is safe: model_cache.py uses only the standard library.
import model_cache  # noqa: E402

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

# Heavy deps this script (and only this script) needs. We install them on
# demand so the bare system Python works too — and so setup.sh does not have
# to install them for users who never download a model through us.
_BOOTSTRAP_DEPS = ["huggingface_hub>=0.23.0", "tqdm>=4.66.0"]


def _in_venv() -> bool:
    """True when running inside a venv (so pip targets that venv, not --user)."""
    return sys.prefix != sys.base_prefix


def _pip_install_missing(packages: list[str]) -> None:
    """Install `packages` into the current interpreter if missing.

    Works for: system python (uses --user), venv python (uses the venv pip).
    Prints a clear error and exits if pip itself is unavailable.
    """
    # Defer the actual import test to caller — we just install here.
    pip_args = [sys.executable, "-m", "pip", "install", "--upgrade"]
    if not _in_venv():
        # System Python: don't pollute system site-packages, use --user.
        pip_args.append("--user")
    pip_args.extend(packages)
    print("  Installing model-download dependencies (one-time):")
    print(f"    {' '.join(pip_args)}")
    try:
        subprocess.check_call(pip_args)
    except subprocess.CalledProcessError as e:
        print("", file=sys.stderr)
        print("ERROR: could not install huggingface_hub + tqdm.", file=sys.stderr)
        print("       The Whisper model download needs these packages.", file=sys.stderr)
        print("       Fix manually with:", file=sys.stderr)
        print(f"         {' '.join(pip_args)}", file=sys.stderr)
        print("       Then re-run: python3 download_model.py", file=sys.stderr)
        sys.exit(2)
    except FileNotFoundError:
        print("ERROR: pip is not available for this Python interpreter.", file=sys.stderr)
        print(f"       Install pip first, then re-run: {sys.executable} -m pip --version",
              file=sys.stderr)
        sys.exit(2)


def _ensure_bootstrap_deps() -> None:
    """Import huggingface_hub + tqdm; install them on demand if missing.

    Re-executes the script after installing so the freshly installed packages
    are visible to the same process (sys.path only refreshes on import for
    some setups).
    """
    missing: list[str] = []
    try:
        import huggingface_hub  # noqa: F401
    except ImportError:
        missing.append("huggingface_hub>=0.23.0")
    try:
        import tqdm  # noqa: F401
    except ImportError:
        missing.append("tqdm>=4.66.0")

    if not missing:
        return

    print("  Model-download dependencies missing — installing them now (one-time).")
    _pip_install_missing(missing)

    # Re-exec so the new packages are picked up cleanly. os.execv replaces the
    # current process — same args, same stdout/stderr, just fresh sys.path.
    print("  Re-launching after install...")
    os.execv(sys.executable, [sys.executable, *sys.argv])


# --------------------------------------------------------------------------- #
# From this point on, huggingface_hub and tqdm are guaranteed to be importable. #
# --------------------------------------------------------------------------- #

import config  # noqa: E402  (repo shared config - resolves LS_MODEL etc.)

from tqdm.auto import tqdm  # noqa: E402


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


def _sanitize_and_report(model_name: str) -> int:
    """Remove corrupt 0-byte leftovers from the local cache; return count."""
    try:
        removed = model_cache.sanitize_model_cache(model_name)
    except Exception as e:  # never block the download because of repair issues
        print(f"  (cache sanitize skipped: {e})")
        return 0
    if removed:
        print("  REPAIR     : removed corrupt 0-byte cache files:")
        for p in removed:
            print(f"    - {p}")
        print("    (an empty vocabulary.json crashes CTranslate2 with")
        print("     [json.exception.parse_error.101] even when a valid")
        print("     vocabulary.txt sits next to it - these files are now gone)")
    return len(removed)


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
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    status_only = any(a in ("--status", "--check") for a in sys.argv[1:])
    repair = "--repair" in sys.argv[1:]

    model_name = args[0] if args else config.MODEL_NAME
    repo_id = _repo_id_for(model_name)
    hf_home = os.environ.get("HF_HOME", "~/.cache/huggingface")

    print(f"  Model     : {model_name}")
    print(f"  Repo      : {repo_id}")
    print(f"  Cache dir : {hf_home}/hub  (outside this repo -> never re-downloaded)")

    if status_only:
        # Pure status check: skip the verification load too, just probe cache.
        hit = _cache_hit(repo_id)
        if hit:
            print("  STATUS    : CACHED (no download needed)")
            print(f"  Path      : {hit}")
            return 0
        print("  STATUS    : NOT CACHED")
        print("  Run `python3 download_model.py` (no flags) to download it now.")
        return 1

    if repair:
        print("  Repair mode: checking the local cache for corrupt leftovers...")
        n = _sanitize_and_report(model_name)
        if n == 0:
            print("  REPAIR     : nothing to repair (no 0-byte files found).")

    _enable_progress_bars()

    # 1) Fast path: already fully cached -> 0 MB, no network at all.
    #    (Sanitize ran above, so a cache polluted with 0-byte phantoms no
    #    longer counts as "complete" in a way that would crash the loader.)
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

    # 2b) Sanity check: a fresh download must not contain 0-byte files.
    _sanitize_and_report(model_name)

    # 3) Verify it loads from disk (also catches broken installs early).
    _load_from_disk(snapshot_dir)
    return 0


if __name__ == "__main__":
    # Ensure deps BEFORE parsing args, so --status works on a bare system too.
    _ensure_bootstrap_deps()
    sys.exit(main())
