"""
LanguageShadow Manager - port 8765

Lightweight always-on process. Responsibilities:
  1. Serve the static web UI (HTML/CSS/JS).
  2. Expose REST API to control the worker (start / stop / freeze / thaw / status).
  3. Receive heartbeats from the worker, declare it dead if too many are missed.
  4. Auto-kill the worker after WORKER_IDLE_TIMEOUT to free RAM.
  5. Proxy the live-transcribe WebSocket from the browser to the worker so the
     browser only ever talks to port 8765 (one firewall rule, one CORS domain).

Memory profile: ~30-50 MB resident. No heavy deps imported here. The only
optional dependency is `httpx` for worker control; we fall back to urllib if
missing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import subprocess
import sys
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Deque, List, Optional

import psutil
import uvicorn
from fastapi import (FastAPI, HTTPException, Request, WebSocket,
                     WebSocketDisconnect)
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
import exam as exam_lib  # pure-stdlib CEFR exam helpers (task library + MD export)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [manager] %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger("manager")

# httpx logs every internal request (health polls, heartbeats) at INFO —
# that would flood the shared log file and the Logs page. Keep warnings+.
for _noisy in ("httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Worker supervisor
# ---------------------------------------------------------------------------
class WorkerSupervisor:
    """Owns the lifecycle of the heavy worker process.

    State machine:
      STOPPED  -> start() -> STARTING -> (heartbeat) -> RUNNING
      RUNNING -> stop()  -> STOPPING  -> STOPPED
      RUNNING -> freeze() -> FROZEN  -> thaw() -> RUNNING
      RUNNING -> (idle timeout) -> STOPPING -> STOPPED

    FROZEN means we SIGSTOP the worker (RAM is NOT freed but it can be thawed
    instantly, useful when the user just paused the practice for a moment).
    """

    def __init__(self) -> None:
        self.state: str = "STOPPED"
        self.process: Optional[subprocess.Popen] = None
        self.last_active: float = 0.0
        self.last_heartbeat: float = 0.0
        self.worker_pid: Optional[int] = None
        self.worker_rss_mb: int = 0
        self.model_loaded: bool = False
        self.worker_busy: bool = False   # active WS session(s) - never idle-kill
        self.start_count: int = 0
        self._worker_out = None
        self._lock = asyncio.Lock()

    async def start(self) -> dict:
        async with self._lock:
            if self.state in ("RUNNING", "STARTING"):
                return {"status": self.state, "msg": "already running"}
            if self.state == "FROZEN":
                return {"status": "FROZEN", "msg": "thaw first"}
            log.info("Spawning worker process...")
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            # Run worker.py as a subprocess of the same Python interpreter.
            cmd = [sys.executable, str(config.BASE_DIR / "worker.py")]
            # Capture the worker's console output in logs/worker.out so startup
            # crashes (import errors, missing model files, ...) are never lost.
            # The worker also logs structured lines to config.LOG_FILE itself;
            # this file catches everything BEFORE/AFTER the logger is set up.
            config.LOG_DIR.mkdir(exist_ok=True)
            self._worker_out = open(config.LOG_DIR / "worker.out", "ab")
            self.process = subprocess.Popen(
                cmd,
                cwd=str(config.BASE_DIR),
                env=env,
                stdout=self._worker_out,
                stderr=subprocess.STDOUT,
                # Put the worker in its own process group so we can kill the
                # whole tree cleanly.
                start_new_session=True,
            )
            self.worker_pid = self.process.pid
            self.state = "STARTING"
            self.start_count += 1
            self.last_active = time.time()
            return {"status": self.state, "pid": self.worker_pid}

    async def _wait_for_running(self, timeout: float = 5.0) -> bool:
        """Poll /health on the worker until it responds, or timeout."""
        import httpx
        url = f"http://{config.WORKER_HOST}:{config.WORKER_PORT}/health"
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < timeout:
            if self.process and self.process.poll() is not None:
                # Worker died during startup.
                log.error("worker exited with code %d", self.process.returncode)
                self.state = "STOPPED"
                return False
            try:
                async with httpx.AsyncClient(timeout=1.0) as c:
                    r = await c.get(url)
                    if r.status_code == 200:
                        self.state = "RUNNING"
                        self.last_heartbeat = time.time()
                        log.info("Worker is up (pid %d)", self.worker_pid)
                        return True
            except Exception:
                await asyncio.sleep(0.2)
        self.state = "STOPPED"
        return False

    async def stop(self) -> dict:
        async with self._lock:
            if self.state == "STOPPED":
                return {"status": "STOPPED", "msg": "already stopped"}
            if self.state == "FROZEN":
                # Need to thaw before we can kill it cleanly.
                await self._thaw_internal()
            if self.process and self.process.poll() is None:
                log.info("Stopping worker pid %d...", self.worker_pid)
                try:
                    os.killpg(os.getpgid(self.worker_pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
                # Wait up to 5s for graceful exit, then SIGKILL.
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(self.process.wait), timeout=5.0
                    )
                except asyncio.TimeoutError:
                    try:
                        os.killpg(os.getpgid(self.worker_pid), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            self.state = "STOPPED"
            self.process = None
            self.worker_pid = None
            self.worker_rss_mb = 0
            self.model_loaded = False
            self.worker_busy = False
            if self._worker_out:
                try:
                    self._worker_out.close()
                except Exception:
                    pass
                self._worker_out = None
            return {"status": "STOPPED"}

    async def freeze(self) -> dict:
        async with self._lock:
            if self.state != "RUNNING" or not self.worker_pid:
                return {"status": self.state, "msg": "not running"}
            try:
                os.kill(self.worker_pid, signal.SIGSTOP)
                self.state = "FROZEN"
                log.info("Worker frozen (pid %d)", self.worker_pid)
                return {"status": "FROZEN", "pid": self.worker_pid}
            except ProcessLookupError:
                self.state = "STOPPED"
                return {"status": "STOPPED", "msg": "process gone"}

    async def thaw(self) -> dict:
        async with self._lock:
            return await self._thaw_internal()

    async def _thaw_internal(self) -> dict:
        if self.state != "FROZEN" or not self.worker_pid:
            return {"status": self.state, "msg": "not frozen"}
        try:
            os.kill(self.worker_pid, signal.SIGCONT)
            self.state = "RUNNING"
            self.last_active = time.time()
            self.last_heartbeat = time.time()
            log.info("Worker thawed (pid %d)", self.worker_pid)
            return {"status": "RUNNING", "pid": self.worker_pid}
        except ProcessLookupError:
            self.state = "STOPPED"
            return {"status": "STOPPED", "msg": "process gone"}

    async def status(self) -> dict:
        # Refresh RAM info even if we don't get a heartbeat.
        if self.worker_pid:
            try:
                p = psutil.Process(self.worker_pid)
                self.worker_rss_mb = p.memory_info().rss // (1024 * 1024)
                if self.state in ("RUNNING", "STARTING") and p.status() == "stopped":
                    self.state = "FROZEN"
            except psutil.NoSuchProcess:
                self.state = "STOPPED"
                self.worker_pid = None
                self.worker_rss_mb = 0
                self.model_loaded = False
        return {
            "state": self.state,
            "pid": self.worker_pid,
            "rss_mb": self.worker_rss_mb,
            "model_loaded": self.model_loaded,
            "idle_seconds": int(time.time() - self.last_active),
            "idle_timeout": config.WORKER_IDLE_TIMEOUT,
            "start_count": self.start_count,
        }

    def touch_active(self) -> None:
        self.last_active = time.time()

    async def heartbeat(self, payload: dict) -> None:
        self.worker_pid = int(payload.get("pid", self.worker_pid or 0)) or None
        self.worker_rss_mb = int(payload.get("rss_mb", self.worker_rss_mb))
        self.model_loaded = bool(payload.get("model_loaded", False))
        # An open WS session counts as real usage. Without this the idle
        # watchdog killed the worker 60 s into a captioning session that was
        # simply quiet for a moment (audio flows only while the user speaks).
        self.worker_busy = int(payload.get("active_sessions", 0) or 0) > 0
        if self.worker_busy:
            self.last_active = time.time()
        self.last_heartbeat = time.time()
        if self.state == "STARTING":
            self.state = "RUNNING"

    async def idle_watchdog(self) -> None:
        """Kill the worker when idle longer than WORKER_IDLE_TIMEOUT."""
        while True:
            await asyncio.sleep(5)
            if self.state != "RUNNING":
                continue
            if self.worker_busy:
                continue  # a live caption session is open - never kill it
            idle = time.time() - self.last_active
            if idle > config.WORKER_IDLE_TIMEOUT:
                log.info("Worker idle for %.1fs, killing to free RAM", idle)
                await self.stop()

    async def heartbeat_watchdog(self) -> None:
        """Mark worker dead if heartbeats stop."""
        while True:
            await asyncio.sleep(config.WORKER_HEARTBEAT_INTERVAL)
            if self.state != "RUNNING":
                continue
            miss = time.time() - self.last_heartbeat
            if miss > config.WORKER_HEARTBEAT_INTERVAL * config.WORKER_HEARTBEAT_MISS:
                log.warning("Worker heartbeat missing for %.1fs, declaring dead", miss)
                await self.stop()

    async def ram_watchdog(self) -> None:
        """Restart worker if its RSS exceeds the hard limit for too long."""
        over_since: Optional[float] = None
        while True:
            await asyncio.sleep(config.WORKER_RAM_SAMPLE_INTERVAL)
            if self.state != "RUNNING" or not self.worker_pid:
                over_since = None
                continue
            if self.worker_rss_mb > config.WORKER_RAM_HARD_LIMIT_MB:
                if over_since is None:
                    over_since = time.time()
                elif time.time() - over_since > 30.0:
                    log.error("Worker RSS %d MB > limit %d MB, restarting",
                              self.worker_rss_mb, config.WORKER_RAM_HARD_LIMIT_MB)
                    await self.stop()
                    await asyncio.sleep(1.0)
                    await self.start()
                    over_since = None
            else:
                over_since = None


supervisor = WorkerSupervisor()


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Manager starting on %s:%d", config.MANAGER_HOST, config.MANAGER_PORT)
    tasks = [
        asyncio.create_task(supervisor.idle_watchdog()),
        asyncio.create_task(supervisor.heartbeat_watchdog()),
        asyncio.create_task(supervisor.ram_watchdog()),
    ]
    # Auto-start worker on boot so the first user request is fast. Comment out
    # this line if you prefer the worker to stay off until a request arrives.
    if os.environ.get("LS_AUTOSTART_WORKER", "0") == "1":
        asyncio.create_task(supervisor.start())
    yield
    for t in tasks:
        t.cancel()
    await supervisor.stop()
    log.info("Manager shutting down")


app = FastAPI(
    title="LanguageShadow Manager",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Static UI — SvelteKit SPA build
# ---------------------------------------------------------------------------
# SvelteKit with adapter-static builds to ui/build/. The output is:
#   index.html           - the entry HTML (with hashed JS/CSS links)
#   _app/immutable/...  - hashed JS/CSS chunks (immutable, long-cacheable)
#   favicon.svg
#   /live, /read, /youtube - per-route prerendered fallbacks (or just /index.html
#                            in SPA mode if fallback: 'index.html' is set)
#
# We mount the whole build dir as static so all assets resolve. Then a catch-all
# route returns index.html for any path that's not an API endpoint or a real
# asset — this is what makes client-side routing work.
if config.STATIC_DIR.exists() and (config.STATIC_DIR / "index.html").exists():
    app.mount("/_app", StaticFiles(directory=str(config.STATIC_DIR / "_app")), name="app_assets")
    # Expose any other static files at the root (favicon etc.)
    app.mount("/favicon.svg", StaticFiles(directory=str(config.STATIC_DIR)), name="favicon")


@app.get("/")
async def index() -> HTMLResponse:
    f = config.STATIC_DIR / "index.html"
    if not f.exists():
        return HTMLResponse(
            "<h1>LanguageShadow</h1>"
            "<p>UI not built. Run <code>./setup.sh</code> (which builds the Svelte UI) "
            "or <code>cd ui && npm run build</code> manually.</p>",
            200,
        )
    return HTMLResponse(f.read_text(encoding="utf-8"))


# NOTE: the SPA catch-all route is registered at the BOTTOM of this file,
# AFTER every API route. Starlette matches routes in registration order, so
# a catch-all defined earlier would shadow /manager/*, /api/* GET endpoints
# and return 404 for all of them.


# ---------------------------------------------------------------------------
# Manager control API
# ---------------------------------------------------------------------------
@app.get("/manager/status")
async def m_status() -> dict:
    return await supervisor.status()


@app.post("/manager/start")
async def m_start() -> dict:
    r = await supervisor.start()
    if r.get("status") in ("STARTING", "RUNNING"):
        await supervisor._wait_for_running(timeout=10.0)
    return await supervisor.status()


@app.post("/manager/stop")
async def m_stop() -> dict:
    return await supervisor.stop()


@app.post("/manager/freeze")
async def m_freeze() -> dict:
    return await supervisor.freeze()


@app.post("/manager/thaw")
async def m_thaw() -> dict:
    return await supervisor.thaw()


@app.post("/manager/heartbeat")
async def m_heartbeat(req: Request) -> dict:
    try:
        payload = await req.json()
    except Exception:
        return {"ok": False}
    await supervisor.heartbeat(payload)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Language config API
# ---------------------------------------------------------------------------
@app.get("/api/languages")
async def list_languages() -> dict:
    out = {}
    for f in sorted(config.LANG_DIR.glob("*.json")):
        try:
            out[f.stem] = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("bad language file %s: %s", f, e)
    return out


# ---------------------------------------------------------------------------
# Caption inbox (browser extension -> manager)
# ---------------------------------------------------------------------------
# The extension POSTs each new caption line here. We keep the last 50 in
# memory; the YouTube Helper page polls /api/captions to fetch them.
# In-memory only - on manager restart they're gone, by design.
class CaptionStore:
    def __init__(self, capacity: int = 50) -> None:
        self._items: Deque[dict] = deque(maxlen=capacity)
        self._lock = asyncio.Lock()

    async def add(self, text: str, source: str = "youtube") -> dict:
        entry = {"ts": time.time(), "text": text, "source": source}
        async with self._lock:
            self._items.append(entry)
        return entry

    async def since(self, ts: float = 0.0) -> list:
        async with self._lock:
            return [e for e in self._items if e["ts"] > ts]

    async def clear(self) -> None:
        async with self._lock:
            self._items.clear()


caption_store = CaptionStore()


class CaptionRequest(BaseModel):
    text: str
    source: str = "youtube"


@app.post("/api/caption")
async def post_caption(req: CaptionRequest) -> dict:
    if not req.text.strip():
        return JSONResponse({"error": "empty text"}, status_code=400)
    entry = await caption_store.add(req.text.strip(), req.source)
    return entry


@app.get("/api/captions")
async def get_captions(since: float = 0.0) -> dict:
    items = await caption_store.since(since)
    return {"captions": items}


@app.delete("/api/captions")
async def clear_captions() -> dict:
    await caption_store.clear()
    return {"ok": True}


@app.get("/api/health")
async def api_health() -> dict:
    """Combined health for the front-end to ping in one round-trip."""
    s = await supervisor.status()
    manager_rss = psutil.Process().memory_info().rss // (1024 * 1024)
    return {
        "manager": {"pid": os.getpid(), "rss_mb": manager_rss},
        "worker": s,
        "ram_budget_mb": config.RAM_BUDGET_MB,
        "model": config.MODEL_NAME,
    }


# ---------------------------------------------------------------------------
# CEFR Exam Trainer API — task library + assessment export
# ---------------------------------------------------------------------------
# Both endpoints are LIGHTWEIGHT (file scanning + markdown building; no model,
# no numpy) so they live on the always-on manager. The heavy part — turning
# the recording into words — still happens in the worker over the existing
# /ws/transcribe WebSocket with mode:"exam" (see worker.py).
@app.get("/api/exam/tasks")
async def exam_tasks() -> dict:
    """All exam tasks from tasks/<LEVEL>/*.md|json (adding a task = dropping
    a file in the folder — no code changes, no restart needed)."""
    return {"levels": list(exam_lib.LEVELS), "tasks": exam_lib.list_tasks()}


class ExamExportRequest(BaseModel):
    level: str
    slug: str
    transcript: str = ""
    words: List[dict] = []
    metrics: Optional[dict] = None
    duration_s: Optional[float] = None
    recorded_at: Optional[str] = None


@app.post("/api/exam/export")
async def exam_export(req: ExamExportRequest) -> dict:
    """Build the ONE self-contained Markdown file the learner pastes into any
    LLM (ChatGPT/Claude/Gemini/local) to get the official-style CEFR score."""
    task = exam_lib.find_task(req.level, req.slug)
    if task is None:
        return JSONResponse(
            {"error": f"unknown task {req.level}/{req.slug}"}, status_code=404)
    if not req.transcript.strip() and not req.words:
        return JSONResponse(
            {"error": "nothing to export: no transcript and no word data"},
            status_code=400)
    metrics = req.metrics or exam_lib.compute_speech_metrics(
        req.words, req.duration_s)
    data = {
        "transcript": req.transcript,
        "words": req.words,
        "metrics": metrics,
        "duration_s": req.duration_s or metrics.get("duration_s"),
        "recorded_at": req.recorded_at,
    }
    markdown = exam_lib.build_assessment_markdown(task, data)
    return {
        "filename": exam_lib.export_filename(task, data),
        "markdown": markdown,
        "task": {k: task.get(k) for k in ("level", "slug", "title", "exam")},
    }


# ---------------------------------------------------------------------------
# REST proxy: /api/assess-speech on the manager forwards to the worker
# ---------------------------------------------------------------------------
# This lets the browser call the SAME endpoint the extension uses, just via
# the manager port (so the browser doesn't need to know about port 8000).
# The extension itself talks directly to port 8000 (per API.md), so it does
# not depend on this proxy. Useful when the web UI wants to send a recorded
# blob for one-shot scoring.
@app.post("/api/assess-speech")
async def m_assess_speech(req: Request) -> JSONResponse:
    s = await supervisor.status()
    if s["state"] != "RUNNING":
        # Auto-start the worker if it's stopped.
        await supervisor.start()
        ok = await supervisor._wait_for_running(timeout=15.0)
        if not ok:
            return JSONResponse(
                {"status": "ERROR", "error": "worker failed to start"},
                status_code=503,
            )
    try:
        body = await req.body()
        import httpx
        async with httpx.AsyncClient(timeout=120.0) as c:
            r = await c.post(
                f"http://{config.WORKER_HOST}:{config.WORKER_PORT}/api/assess-speech",
                content=body,
                headers={"Content-Type": "application/json"},
            )
        return JSONResponse(r.json(), status_code=r.status_code)
    except Exception as e:
        return JSONResponse(
            {"status": "ERROR", "error": f"proxy failed: {e}"},
            status_code=502,
        )


# Also proxy /api/logs and /api/stats from the worker (so the web UI can use
# either port — useful in dev).
@app.get("/api/logs")
async def m_logs_proxy(limit: int = 10) -> JSONResponse:
    s = await supervisor.status()
    if s["state"] != "RUNNING":
        return JSONResponse({"logs": []})
    import httpx
    async with httpx.AsyncClient(timeout=5.0) as c:
        r = await c.get(f"http://{config.WORKER_HOST}:{config.WORKER_PORT}/api/logs?limit={limit}")
    return JSONResponse(r.json())


# ---------------------------------------------------------------------------
# System log viewer API — reads logs/languageshadow.log from disk.
# ---------------------------------------------------------------------------
# Unlike /api/logs (practice history, lives in the WORKER's memory and
# disappears when the worker is idle-killed), this endpoint works ALWAYS -
# the manager can serve it even when the worker is stopped. Both processes
# log structured lines to the same file with " [manager] " / " [worker] "
# tags, which we parse back into structured entries for the Logs page.
_LOG_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+)\s+\[(manager|worker)\]\s+(\w+)\s*[: ]?\s?(.*)$"
)

# How much of the log file we read from the end when asked for a tail.
_LOG_TAIL_BYTES = 512 * 1024


def _read_log_entries(tail_bytes: int = _LOG_TAIL_BYTES) -> list:
    """Parse the shared log file into [{ts, source, level, msg}] entries.

    Multi-line records (tracebacks etc.) are folded into the previous
    entry's message. Unparseable leading lines become source='system'.
    """
    f = config.LOG_FILE
    if not f.exists():
        return []
    size = f.stat().st_size
    with open(f, "rb") as fh:
        if size > tail_bytes:
            fh.seek(-tail_bytes, os.SEEK_END)
        fh.readline()  # drop the (probably partial) first line
        text = fh.read().decode("utf-8", errors="replace")

    entries: list = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        m = _LOG_LINE_RE.match(raw)
        if m:
            ts_str, source, level, msg = m.groups()
            try:
                ts = time.mktime(time.strptime(ts_str, "%Y-%m-%d %H:%M:%S,%f"))
            except ValueError:
                ts = 0.0
            entries.append({"ts": ts, "ts_str": ts_str, "source": source,
                            "level": level.lower(), "msg": msg})
        elif entries:
            # Continuation of the previous entry (traceback, etc.)
            entries[-1]["msg"] += "\n" + raw
        else:
            entries.append({"ts": 0.0, "ts_str": "", "source": "system",
                            "level": "info", "msg": raw})
    return entries


@app.get("/api/logs/system")
async def m_logs_system(tail: int = 300, source: str = "all") -> dict:
    """Runtime log lines from manager + worker (from the shared log file).

    Query params:
      tail   - max number of entries to return (default 300)
      source - all | manager | worker | system
    """
    tail = max(10, min(int(tail), 2000))
    entries = _read_log_entries()
    if source and source != "all":
        entries = [e for e in entries if e["source"] == source]
    return {
        "file": str(config.LOG_FILE),
        "size_bytes": config.LOG_FILE.stat().st_size if config.LOG_FILE.exists() else 0,
        "lines": entries[-tail:],
    }


@app.get("/api/logs/output")
async def m_logs_output(tail_lines: int = 200) -> dict:
    """Raw captured console output of the worker (logs/worker.out).

    This is where crashes that happen before the logger is configured land
    (import errors, missing shared libraries, download failures, ...).
    """
    tail_lines = max(10, min(int(tail_lines), 2000))
    f = config.LOG_DIR / "worker.out"
    if not f.exists():
        return {"file": str(f), "lines": []}
    size = f.stat().st_size
    with open(f, "rb") as fh:
        if size > _LOG_TAIL_BYTES:
            fh.seek(-_LOG_TAIL_BYTES, os.SEEK_END)
        data = fh.read().decode("utf-8", errors="replace")
    lines = data.splitlines()[-tail_lines:]
    return {"file": str(f), "lines": lines}


@app.get("/api/stats")
async def m_stats_proxy() -> JSONResponse:
    s = await supervisor.status()
    if s["state"] != "RUNNING":
        return JSONResponse({})
    import httpx
    async with httpx.AsyncClient(timeout=5.0) as c:
        r = await c.get(f"http://{config.WORKER_HOST}:{config.WORKER_PORT}/api/stats")
    return JSONResponse(r.json())


# ---------------------------------------------------------------------------
# WebSocket proxy: browser <-> manager <-> worker
# ---------------------------------------------------------------------------
# Why proxy? The browser should only know about port 8765. The worker is on a
# separate port (and possibly bound to a private interface), and we want a
# single CORS / firewall surface. We also use the proxy as a hook to make
# sure the worker is running before letting the stream through.
@app.websocket("/ws/transcribe")
async def ws_proxy(ws_in: WebSocket) -> None:
    await ws_in.accept()

    # Make sure the worker is up. If it's stopped or frozen, start/thaw it.
    s = await supervisor.status()
    if s["state"] == "STOPPED":
        await supervisor.start()
        await supervisor._wait_for_running(timeout=15.0)
        s = await supervisor.status()
    elif s["state"] == "FROZEN":
        await supervisor.thaw()
    elif s["state"] == "STARTING":
        # The worker is booting (auto-start was just triggered, e.g. by the
        # page's pre-warm). Wait for it instead of slamming the socket shut -
        # closing here made the browser fail its first connection with
        # "ws closed before ready" whenever the user clicked Start quickly.
        for _ in range(30):  # up to ~15 s
            await asyncio.sleep(0.5)
            s = await supervisor.status()
            if s["state"] != "STARTING":
                break
    if s["state"] != "RUNNING":
        await ws_in.close(code=1011, reason="worker not available")
        return

    # Connect to worker's /ws/transcribe.
    import websockets
    try:
        async with websockets.connect(
            f"ws://{config.WORKER_HOST}:{config.WORKER_PORT}/ws/transcribe",
            max_size=None,
        ) as ws_out:
            # First frame from browser must be the JSON config.
            try:
                cfg = await asyncio.wait_for(ws_in.receive_text(), timeout=10.0)
                await ws_out.send(cfg)
                # Forward the worker's "ready" frame back to the browser.
                ready = await ws_out.recv()
                await ws_in.send_text(ready)
            except WebSocketDisconnect:
                return

            # Two-direction pump until either side disconnects.
            async def in_to_out() -> None:
                try:
                    while True:
                        msg = await ws_in.receive()
                        # Every forwarded frame is REAL usage: refresh the idle
                        # timer so a long, active caption session is never
                        # killed (the old code only touched the timer AFTER the
                        # session ended, so the watchdog saw 100% idle the whole
                        # time and cut the worker off mid-recording).
                        supervisor.touch_active()
                        if msg.get("text") is not None:
                            await ws_out.send(msg["text"])
                            if msg["text"] == "END":
                                break
                        elif msg.get("bytes") is not None:
                            await ws_out.send(msg["bytes"])
                        else:
                            break
                except WebSocketDisconnect:
                    pass

            async def out_to_in() -> None:
                try:
                    async for msg in ws_out:
                        if isinstance(msg, bytes):
                            await ws_in.send_bytes(msg)
                        else:
                            await ws_in.send_text(msg)
                except Exception:
                    pass

            await asyncio.gather(in_to_out(), out_to_in())
            supervisor.touch_active()
    except Exception as e:
        log.warning("ws proxy failed: %s", e)
        try:
            await ws_in.close(code=1011, reason=str(e))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# SPA catch-all — MUST stay after every API route (registration order =
# match priority in Starlette). Returns index.html for client-side SvelteKit
# routes (/live, /read, /youtube, etc.). Real static assets under /_app/ are
# handled by the StaticFiles mount above.
# ---------------------------------------------------------------------------
@app.get("/{path:path}")
async def spa_fallback(path: str, request: Request) -> HTMLResponse:
    # Don't shadow the API endpoints (they are all registered above and take
    # priority; reaching here with an api/manager path means it truly does
    # not exist).
    if path.startswith(("api/", "manager/", "ws", "static/", "_app/")):
        return HTMLResponse("not found", 404)

    # If a real file exists at this path under the build dir (e.g. favicon.svg),
    # let it serve.
    candidate = config.STATIC_DIR / path
    if candidate.is_file():
        return FileResponse(str(candidate))

    # Otherwise return the SPA shell for client-side routing.
    f = config.STATIC_DIR / "index.html"
    if f.exists():
        return HTMLResponse(f.read_text(encoding="utf-8"))
    return HTMLResponse("not found", 404)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(
        app,
        host=config.MANAGER_HOST,
        port=config.MANAGER_PORT,
        log_level="warning",
        access_log=False,
        workers=1,
    )
