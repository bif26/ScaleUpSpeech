"""
LanguageShadow Worker - port 8000

Heavy AI process. Spawned on demand by the Manager. Holds the whisper model
and exposes:
  - GET  /health
  - POST /api/assess-speech           (one-shot assessment, matches extension API.md)
  - WS   /ws/transcribe               (streaming live transcription + scoring for the web UI)
  - GET  /api/logs                    (recent assessments)
  - GET  /api/stats                   (aggregate stats)
  - POST /heartbeat                   (manager heartbeat)

API.md contract for /api/assess-speech:
  Request:  {"audio_base64": "<webm/opus base64>", "reference_text": "...", "language": "de-DE"}
  Response: {"status": "OK",
             "overall": 0-100,
             "subscores": {"accuracy", "fluency", "prosody", "completeness"},
             "pace": {"wpm", "duration_ms"},
             "words": [{"word","accuracy","errorType"}],
             "recognized": "..."}

Audio decoding:
  - The browser extension sends webm/opus (Chrome) or ogg/opus (Firefox).
  - We decode to 16 kHz mono PCM s16le via ffmpeg (subprocess), then numpy.
  - No intermediate file: pipe in / pipe out for low RAM.

RAM (typical, CPU int8):
  tiny:    ~150 MB
  base:    ~300 MB
  small:   ~500 MB   (default)
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import statistics
import struct
import subprocess
import tempfile
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

import numpy as np
import psutil
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import config
from scoring import score_assessment, incremental_score

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [worker] %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger("worker")

# Same as manager: httpx heartbeat logs would flood the shared log file.
for _noisy in ("httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# ffmpeg audio decoder (webm/opus / ogg/opus / wav -> 16k mono PCM float32)
# ---------------------------------------------------------------------------
def decode_audio_to_pcm(audio_bytes: bytes) -> np.ndarray:
    """Decode any ffmpeg-supported audio container to 16 kHz mono float32.

    Piped in / piped out — no temp file, minimal RAM.
    """
    cmd = [
        "ffmpeg",
        "-hide_banner", "-loglevel", "error",
        "-i", "pipe:0",
        "-f", "f32le",                # 32-bit float little-endian
        "-acodec", "pcm_f32le",
        "-ar", str(config.SAMPLE_RATE),
        "-ac", "1",
        "pipe:1",
    ]
    proc = subprocess.run(
        cmd,
        input=audio_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"ffmpeg decode failed: {err}")
    audio = np.frombuffer(proc.stdout, dtype=np.float32)
    # Guard against clips.
    audio = np.clip(audio, -1.0, 1.0)
    return audio.astype(np.float32)


# ---------------------------------------------------------------------------
# Model holder (loaded lazily)
# ---------------------------------------------------------------------------
class ModelHolder:
    def __init__(self) -> None:
        self._model = None
        self._lock = asyncio.Lock()
        self._load_time = 0.0

    async def get(self):
        async with self._lock:
            if self._model is None:
                log.info("Loading whisper model '%s' (compute=%s, device=%s)...",
                         config.MODEL_NAME, config.MODEL_COMPUTE_TYPE, config.MODEL_DEVICE)
                t0 = time.perf_counter()
                from faster_whisper import WhisperModel
                # cpu_threads MUST be an int: ctranslate2 rejects None
                # ("TypeError: incompatible constructor arguments").
                # config.MODEL_CPU_THREADS is always an int (0 = auto).
                model_kwargs = dict(
                    device=config.MODEL_DEVICE,
                    compute_type=config.MODEL_COMPUTE_TYPE,
                    cpu_threads=config.MODEL_CPU_THREADS,
                )
                try:
                    # Fast path: model already in the local HF cache.
                    # Pure disk, no network, instant start, works offline.
                    self._model = WhisperModel(
                        config.MODEL_NAME, local_files_only=True, **model_kwargs)
                except Exception:
                    # Model truly missing: download it once (visible progress
                    # bars from huggingface_hub). Every later run takes the
                    # fast path above and never downloads again.
                    log.info("Model not in local cache — downloading once...")
                    self._model = WhisperModel(config.MODEL_NAME, **model_kwargs)
                self._load_time = time.perf_counter() - t0
                log.info("Whisper model loaded in %.2fs (PID %d, RSS %d MB)",
                         self._load_time, psutil.Process().pid,
                         psutil.Process().memory_info().rss // (1024 * 1024))
            return self._model

    def is_loaded(self) -> bool:
        return self._model is not None


model_holder = ModelHolder()


# ---------------------------------------------------------------------------
# Log / stat store (in-memory; manager may persist these too if needed)
# ---------------------------------------------------------------------------
class LogStore:
    def __init__(self, capacity: int = 200) -> None:
        self._items: Deque[dict] = deque(maxlen=capacity)
        self._lock = asyncio.Lock()
        self._stats = {
            "total_assessments": 0,
            "total_audio_seconds": 0.0,
            "avg_overall": 0.0,
            "avg_accuracy": 0.0,
            "avg_fluency": 0.0,
        }

    async def add(self, entry: dict) -> None:
        async with self._lock:
            self._items.append(entry)
            n = self._stats["total_assessments"] + 1
            self._stats["total_assessments"] = n
            self._stats["total_audio_seconds"] += entry.get("pace", {}).get("duration_ms", 0) / 1000.0
            for k in ("avg_overall", "avg_accuracy", "avg_fluency"):
                key_field = {"avg_overall": "overall",
                             "avg_accuracy": ("subscores", "accuracy"),
                             "avg_fluency": ("subscores", "fluency")}[k]
                if isinstance(key_field, tuple):
                    cur = entry.get(key_field[0], {}).get(key_field[1], 0) or 0
                else:
                    cur = entry.get(key_field, 0) or 0
                self._stats[k] = (self._stats[k] * (n - 1) + cur) / n

    async def recent(self, limit: int = 10) -> List[dict]:
        async with self._lock:
            return list(self._items)[-limit:]

    async def stats(self) -> dict:
        async with self._lock:
            return dict(self._stats)


store = LogStore()


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Worker starting on %s:%d", config.WORKER_HOST, config.WORKER_PORT)
    # Sanity check ffmpeg is on PATH (we need it for webm/opus decoding).
    if not _ffmpeg_available():
        log.error("ffmpeg NOT found on PATH. /api/assess-speech will fail. "
                  "On Arch: sudo pacman -S ffmpeg")
    heartbeat_task = asyncio.create_task(_heartbeat_loop())
    yield
    heartbeat_task.cancel()
    log.info("Worker shutting down")


def _ffmpeg_available() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except Exception:
        return False


app = FastAPI(
    title="LanguageShadow Worker",
    version="1.4.0",
    lifespan=lifespan,
)

# CORS: the existing browser extension talks directly to port 8000
# (per API.md). Allow any origin so it works from any extension context.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _heartbeat_loop() -> None:
    """Tell the manager we are alive every WORKER_HEARTBEAT_INTERVAL seconds."""
    import httpx
    url = f"http://{config.MANAGER_HOST}:{config.MANAGER_PORT}/manager/heartbeat"
    while True:
        try:
            async with httpx.AsyncClient(timeout=2.0) as c:
                rss = psutil.Process().memory_info().rss // (1024 * 1024)
                await c.post(url, json={
                    "pid": psutil.Process().pid,
                    "rss_mb": rss,
                    "model_loaded": model_holder.is_loaded(),
                })
        except Exception:
            # Manager may be briefly unreachable during restarts; not fatal.
            pass
        await asyncio.sleep(config.WORKER_HEARTBEAT_INTERVAL)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _language_for_whisper(lang_tag: str) -> Optional[str]:
    """Convert BCP-47 (de-DE) to whisper's 2-letter code (de).
    Pass None to let whisper auto-detect."""
    if not lang_tag or lang_tag.lower() == "auto":
        return None
    # Strip region: de-DE -> de, en-US -> en
    base = lang_tag.split("-")[0].lower()
    return base


def _transcribe(audio: np.ndarray, language: Optional[str]) -> List[dict]:
    """Run whisper and return list of segment dicts with word timestamps."""
    model = model_holder._model
    if model is None:
        raise RuntimeError("model not loaded")
    segments, info = model.transcribe(
        audio,
        language=language,
        vad_filter=config.VAD_ENABLED,
        vad_parameters={"threshold": config.VAD_THRESHOLD},
        word_timestamps=True,
    )
    out = []
    for s in segments:
        out.append({
            "text": s.text,
            "start": float(s.start),
            "end": float(s.end),
            "words": [
                {"word": w.word, "start": float(w.start),
                 "end": float(w.end), "probability": float(w.probability)}
                for w in (s.words or [])
            ],
        })
    return out


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
async def health() -> dict:
    rss = psutil.Process().memory_info().rss // (1024 * 1024)
    return {
        "status": "ok",
        "pid": psutil.Process().pid,
        "rss_mb": rss,
        "model_loaded": model_holder.is_loaded(),
        "model_name": config.MODEL_NAME,
        "ffmpeg_available": _ffmpeg_available(),
        "uptime_s": time.time() - _WORKER_START,
    }


class AssessRequest(BaseModel):
    audio_base64: str
    reference_text: str
    language: str = "en"
    # Some old clients may still send target_language.
    target_language: Optional[str] = None


@app.post("/api/assess-speech")
async def assess_speech(req: AssessRequest) -> JSONResponse:
    """Extension-facing endpoint. See API.md for the contract."""
    # Normalise the language tag.
    lang = req.language or req.target_language or "en"
    whisper_lang = _language_for_whisper(lang)

    # Decode the base64 audio.
    try:
        raw = base64.b64decode(req.audio_base64)
    except Exception as e:
        return JSONResponse({"status": "ERROR", "error": f"bad base64: {e}"},
                            status_code=400)

    # Decode webm/opus / ogg/opus / wav -> 16k mono float32.
    try:
        audio = await asyncio.to_thread(decode_audio_to_pcm, raw)
    except Exception as e:
        return JSONResponse({"status": "ERROR", "error": f"audio decode failed: {e}"},
                            status_code=400)
    if len(audio) < int(config.SAMPLE_RATE * 0.1):
        return JSONResponse({"status": "ERROR",
                             "error": "Audio too short (<0.1s). Speak longer."},
                            status_code=400)

    # Load model (lazy on first call).
    try:
        await model_holder.get()
    except Exception as e:
        log.exception("model load failed")
        return JSONResponse({"status": "ERROR", "error": f"model load failed: {e}"},
                            status_code=500)

    # Transcribe.
    try:
        segments = await asyncio.to_thread(_transcribe, audio, whisper_lang)
    except Exception as e:
        log.exception("transcribe failed")
        return JSONResponse({"status": "ERROR", "error": str(e)}, status_code=500)

    # Score.
    result = score_assessment(
        reference_text=req.reference_text,
        whisper_segments=segments,
    )
    if result.get("status") != "OK":
        return JSONResponse(result, status_code=200)  # error message in body

    # Persist to log store + return.
    log_entry = {
        "ts": time.time(),
        "reference": req.reference_text,
        "language": lang,
        **result,
    }
    await store.add(log_entry)
    return JSONResponse(result)


@app.get("/api/logs")
async def get_logs(limit: int = 10) -> dict:
    return {"logs": await store.recent(limit)}


@app.get("/api/stats")
async def get_stats() -> dict:
    return await store.stats()


@app.post("/heartbeat")
async def heartbeat() -> dict:
    return {"ok": True}


# ---------------------------------------------------------------------------
# WebSocket endpoint for live streaming (used by the web UI, not the extension)
# ---------------------------------------------------------------------------
class StreamingSession:
    # Rolling-window tuning for free-speech (Live Caption) mode. We keep only
    # the audio tail in the buffer and move finished sentences into
    # settled_text, so transcription latency stays constant even after hours
    # of captioning (and RAM stays flat - important for the 2.3 GB budget).
    SETTLE_MARGIN_S = 1.2   # a segment ends >= this far from buffer end -> settled
    TAIL_KEEP_S = 20.0      # never transcribe more than the last N seconds

    def __init__(self, ws: WebSocket, *, reference_text: str = "",
                 live_scoring: bool = True, target_language: str = "en") -> None:
        self.ws = ws
        self.reference_text = reference_text
        self.live_scoring = live_scoring
        self.target_language = target_language
        self.buffer: np.ndarray = np.zeros(0, dtype=np.float32)
        self.committed_segments: List[dict] = []
        self.settled_text: str = ""      # free-speech mode: finished transcript
        self._last_partial: str = ""     # dedupe identical partial frames
        self.start_time = time.time()
        self.session_id = str(uuid.uuid4())

    async def append(self, pcm: np.ndarray) -> None:
        self.buffer = np.concatenate([self.buffer, pcm])
        min_samples = int(config.SAMPLE_RATE * 0.6)
        if len(self.buffer) < min_samples:
            return
        try:
            segments = await asyncio.to_thread(_transcribe, self.buffer,
                                                _language_for_whisper(self.target_language))
        except Exception as e:
            log.debug("transcribe failed during streaming: %s", e)
            return

        if not self.reference_text:
            # Live Caption mode: emit the running transcript and roll the
            # buffer forward so we only ever transcribe the recent tail.
            await self._emit_partial(segments)
            self._settle(segments)
            return

        new_count = sum(len(s.get("words") or []) for s in segments)
        old_count = sum(len(s.get("words") or []) for s in self.committed_segments)
        self.committed_segments = segments
        if new_count > old_count and self.live_scoring:
            await self._emit_incremental()

    async def _emit_partial(self, segments: List[dict]) -> None:
        text = " ".join(s.get("text", "").strip() for s in segments).strip()
        full = (self.settled_text + " " + text).strip()
        if not full or full == self._last_partial:
            return
        self._last_partial = full
        payload = {
            "type": "partial",
            "session_id": self.session_id,
            "text": full,
            "words": len(full.split()),
            "elapsed_s": round(time.time() - self.start_time, 2),
        }
        try:
            await self.ws.send_text(json.dumps(payload))
        except Exception:
            log.debug("ws send failed during partial")

    def _settle(self, segments: List[dict]) -> None:
        """Move finished segments out of the rolling buffer into settled_text.

        A segment is 'settled' when it ends comfortably before the end of the
        buffered audio (i.e. whisper is unlikely to revise it further).
        """
        buf_s = len(self.buffer) / config.SAMPLE_RATE
        settled_until = None  # audio seconds up to which everything is settled
        for s in segments:
            if s.get("end", 0.0) <= buf_s - self.SETTLE_MARGIN_S:
                piece = s.get("text", "").strip()
                if piece:
                    self.settled_text = (self.settled_text + " " + piece).strip()
                    settled_until = s["end"]
        if settled_until is not None:
            keep_from = max(0.0, settled_until - 0.25)
            self.buffer = self.buffer[int(keep_from * config.SAMPLE_RATE):]
        elif buf_s > self.TAIL_KEEP_S:
            # Safety valve: extremely long utterance with no segment boundary -
            # still cap the buffer so latency cannot grow without bound.
            self.buffer = self.buffer[-int(self.TAIL_KEEP_S * config.SAMPLE_RATE):]

    async def _emit_incremental(self) -> None:
        if not self.reference_text:
            return
        payload = incremental_score(self.reference_text, self.committed_segments)
        payload["type"] = "incremental"
        payload["session_id"] = self.session_id
        try:
            await self.ws.send_text(json.dumps(payload))
        except Exception:
            log.debug("ws send failed during incremental")

    async def finalize(self) -> dict:
        try:
            segments = await asyncio.to_thread(_transcribe, self.buffer,
                                                _language_for_whisper(self.target_language))
        except Exception as e:
            log.exception("finalize transcribe failed: %s", e)
            segments = []

        if self.reference_text:
            payload = score_assessment(self.reference_text, segments)
            if payload.get("status") == "OK":
                await store.add({
                    "ts": time.time(),
                    "reference": self.reference_text,
                    "language": self.target_language,
                    **payload,
                })
        else:
            # Free-speech / Live Caption session: recognized text is the
            # settled transcript plus whatever is still in the rolling tail.
            tail = " ".join(s.get("text", "").strip() for s in segments).strip()
            recognized = (self.settled_text + " " + tail).strip()
            payload = {
                "status": "OK",
                "recognized": recognized,
                "words": len(recognized.split()),
                "duration_s": round(time.time() - self.start_time, 2),
                "segments": segments,
            }
        payload["type"] = "final"
        payload["session_id"] = self.session_id
        payload["duration_s"] = round(time.time() - self.start_time, 2)
        return payload


@app.websocket("/ws/transcribe")
async def ws_transcribe(ws: WebSocket) -> None:
    await ws.accept()
    try:
        cfg_raw = await asyncio.wait_for(ws.receive_text(), timeout=10.0)
        cfg = json.loads(cfg_raw)
    except Exception as e:
        log.warning("ws config missing: %s", e)
        await ws.close(code=1008)
        return

    session = StreamingSession(
        ws,
        reference_text=cfg.get("reference_text", ""),
        live_scoring=cfg.get("live_scoring", True),
        target_language=cfg.get("target_language", config.DEFAULT_LANGUAGE),
    )
    await ws.send_text(json.dumps({
        "type": "ready",
        "session_id": session.session_id,
        "sample_rate": config.SAMPLE_RATE,
        "chunk_ms": config.CHUNK_MS,
    }))

    try:
        while True:
            msg = await ws.receive()
            if msg.get("text") == "END":
                final = await session.finalize()
                await ws.send_text(json.dumps(final))
                break
            if msg.get("bytes") is None:
                continue
            try:
                pcm = np.frombuffer(msg["bytes"], dtype=np.float32)
            except Exception as e:
                log.debug("bad pcm: %s", e)
                continue
            await session.append(pcm)
    except WebSocketDisconnect:
        log.info("ws disconnected (session %s)", session.session_id)
    except Exception as e:
        log.exception("ws error: %s", e)
    finally:
        try:
            await ws.close()
        except Exception:
            pass


_WORKER_START = time.time()


def _try_import_uvloop() -> bool:
    try:
        import uvloop  # noqa
        return True
    except Exception:
        return False


if __name__ == "__main__":
    uvicorn.run(
        app,
        host=config.WORKER_HOST,
        port=config.WORKER_PORT,
        log_level="warning",
        access_log=False,
        workers=1,
        loop="uvloop" if _try_import_uvloop() else "asyncio",
    )
