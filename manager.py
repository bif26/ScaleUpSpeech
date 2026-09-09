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
import signal
import subprocess
import sys
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Deque, Optional

import psutil
import uvicorn
from fastapi import (FastAPI, HTTPException, Request, WebSocket,
                     WebSocketDisconnect)
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config

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
        self.start_count: int = 0
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
            self.process = subprocess.Popen(
                cmd,
                cwd=str(config.BASE_DIR),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
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
        self.last_heartbeat = time.time()
        if self.state == "STARTING":
            self.state = "RUNNING"

    async def idle_watchdog(self) -> None:
        """Kill the worker when idle longer than WORKER_IDLE_TIMEOUT."""
        while True:
            await asyncio.sleep(5)
            if self.state != "RUNNING":
                continue
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


@app.get("/{path:path}")
async def spa_fallback(path: str, request: Request) -> HTMLResponse:
    """Catch-all that returns index.html for client-side SvelteKit routes
    (/live, /read, /youtube, etc.). Real static assets under /_app/ are
    handled by the StaticFiles mount above. API routes are declared before
    this handler so they take precedence.
    """
    # Don't shadow the API endpoints (they're declared before this catch-all).
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


# (legacy per-route HTML handlers removed — SvelteKit SPA handles client-side
# routing from index.html)


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
