# LanguageShadow

Privacy-first pronunciation coach that runs **entirely on your machine**.
Listens to your microphone, transcribes with Whisper, and scores your
pronunciation word-by-word against any reference text — like a typing-test
site, but for speaking.

Built for **Arch Linux + Hyprland** on an 8 GB machine that already uses
~2.8 GB for OS + browser. The whole app fits inside a 2–2.3 GB RAM budget.

---

## Two-process architecture

```
+---------------------------+                  +---------------------------+
|  Manager (port 8765)      |  spawn / kill    |  Worker (port 8000)       |
|  - Web UI (static HTML)   | ---------------->|  - faster-whisper model   |
|  - REST control API       |  heartbeat       |  - WebSocket transcription|
|  - WS proxy to worker     | <----------------|  - pronunciation scoring  |
|  - auto idle-kill (60 s)  |                  |  - on-demand, RAM-heavy   |
|  ~30-50 MB, always on     |                  |  ~150-700 MB, on demand   |
+---------------------------+                  +---------------------------+
        ^                                                ^
        |                                                |
        | browser talks only to port 8765                | spawned as subprocess
        | (one firewall rule, one CORS origin)           | of manager.py
        |                                                |
   +----+----+                                      +----+----+
   | browser | (Web UI + mic capture via AudioWorklet) | YouTube extension |
   +---------+                                      +---------+
```

| Process | Port | RAM (typical) | When alive |
|---|---|---|---|
| Manager | 8765 | 30-50 MB | always (boot) |
| Worker  | 8000 | 150-700 MB | on-demand, auto-killed after 60 s idle |

### Why two ports?

- **RAM budget**: the heavy Whisper model only lives when you're speaking. The
  manager stays tiny so the rest of your system has RAM to breathe.
- **Single browser surface**: the browser only talks to port 8765. The
  manager proxies the live WebSocket to the worker, so you don't need to
  punch a second firewall hole or change CORS for the AI port.
- **Lifecycle control**: start / stop / **freeze / thaw** the AI process
  from the UI. Freeze keeps the model warm but stops it using CPU when you
  just pause for a few minutes.

---

## Quick start

```bash
cd languageshadow
./setup.sh              # creates .venv, installs deps, pre-downloads model
./start_manager.sh      # starts the manager in the background
# open http://127.0.0.1:8765/ in your browser
```

To stop everything:

```bash
./stop_backend.sh
```

### Auto-start on boot (systemd)

```bash
# 1. Edit the service file and replace /path/to/languageshadow with the real path:
$EDITOR systemd/languageshadow-manager.service

# 2. Install:
sudo cp systemd/languageshadow-manager.service /etc/systemd/system/
systemctl --user daemon-reload
systemctl --user enable --now languageshadow-manager
```

(use `systemctl --user` so it runs as your user, not root, which means
microphone permissions and Hyprland display access work without extra setup)

---

## The three pages

### 1. `/live` — Live Caption
Type a short reference text, click **Start speaking**, and each word turns
green / red the instant you say it. Just like Monkeytype / 10fastfingers,
but for speaking. Useful for short bursts of pronunciation practice.

Three sliders tune scoring strictness in real time:
- **Mic sensitivity** — amplifies quiet speech for the VU meter (does not affect scoring).
- **Target WPM (min)** — fluency is 100 above this rate, scaled down below it.
- **Pause threshold (s)** — gaps between words longer than this count as hesitation
  pauses and subtract from fluency. Smaller = stricter.

### 2. `/read` — Read Text
Paste a passage from a book (the default is the opening of *A Tale of Two
Cities*). Click **Start reading** and read it aloud. At the end you get:
- overall pronunciation score (0–100)
- 4 sub-scores: **accuracy** / **fluency** / **prosody** / **completeness** (each 0–100)
- CEFR level (A2 / B1 / B2 / B2+) based on overall + WPM
- per-word chips: green ≥ 80, yellow ≥ 60, red below; omitted struck through; inserted italicised
- recognised text (what whisper heard)
- pause count + matched / mispronounced / inserted / omitted breakdown

Optionally enable **live scoring** so you see your progress as you speak.

### 3. `/youtube` — YouTube Helper
Use your existing browser extension — it sends the caption line it captured
and the user's recording to `POST http://127.0.0.1:8000/api/assess-speech`
(per the API.md contract). The server returns the standard scoring JSON
(overall / subscores / pace / words / recognized) and the extension's
panel renders it directly.

The bundled extension on the `extension/` folder is provided as a fallback
for capturing captions if you don't already have one.

---

## Browser extension installation

1. Open `chrome://extensions` (Chromium, Chrome, Brave, Vivaldi, Edge) or
   `about:debugging#/runtime/this-firefox` → "This Firefox" → "Load
   Temporary Add-on".
2. Enable **Developer mode** (top-right).
3. Click **Load unpacked** and select the `extension/` folder.
4. Pin the LanguageShadow icon. Open a YouTube video with captions on,
   click the icon, flip the switch to **Send YouTube captions**.

---

## API reference

The worker (port 8000) speaks **exactly the contract your existing extension
expects** (API.md v1.4.0). The manager (port 8765) proxies the same endpoints
so the browser UI doesn't need to know about port 8000.

### Extension-facing endpoint (port 8000, CORS enabled)

| Method | Path | Description |
|---|---|---|
| POST | `/api/assess-speech` | Score a recorded take. Accepts webm/opus base64. |
| GET | `/health` | Worker health + RAM + model status |
| GET | `/api/logs?limit=N` | Recent takes |
| GET | `/api/stats` | Aggregate stats |
| WS  | `/ws/transcribe` | Live audio stream (used by the web UI, not the extension) |

#### `/api/assess-speech` request

```json
{
  "audio_base64": "<base64 of webm/opus or ogg/opus, ≤ 30 s>",
  "reference_text": "Ich habe heute einen langen Tag gehabt.",
  "language": "de-DE"
}
```

The server decodes the webm/opus via ffmpeg (no temp file, piped I/O), runs
faster-whisper with word-level timestamps + confidence, aligns against the
reference using **Metaphone phonetic matching** (so "colour" / "color" / "kaler"
all count as the same word if they sound the same), and returns the API.md
contract shape:

```json
{
  "status": "OK",
  "overall": 84,
  "subscores": { "accuracy": 88, "fluency": 81, "prosody": 76, "completeness": 100 },
  "pace": { "wpm": 118, "duration_ms": 4200 },
  "words": [
    { "word": "Ich", "accuracy": 96, "errorType": "None" },
    { "word": "heute", "accuracy": 74, "errorType": "Mispronunciation" },
    { "word": "einen", "accuracy": 0, "errorType": "Omitted" },
    ...
  ],
  "recognized": "ich habe heute langen tag gehabt",
  "cefr": "B2",
  "pause_count": 0,
  "n_matched": 6,
  "n_mispronounced": 1,
  "n_omitted": 1,
  "n_inserted": 0
}
```

**errorType** values: `None` / `Mispronunciation` / `Omitted` / `Inserted`
(extra beyond the reference text).

The extra fields (`cefr`, `pause_count`, `n_*`, `recognized`) are debug-level
extras. The panel ignores them per API.md.

### Manager control API (port 8765)

| Method | Path | Description |
|---|---|---|
| GET | `/manager/status` | Worker state, RAM, model loaded? |
| POST | `/manager/start` | Spawn the worker (lazy on first request anyway) |
| POST | `/manager/stop` | Kill worker to free RAM |
| POST | `/manager/freeze` | SIGSTOP the worker (instant resume, RAM kept) |
| POST | `/manager/thaw` | SIGCONT the worker |
| POST | `/manager/heartbeat` | Internal: worker heartbeats here |

The manager also proxies `POST /api/assess-speech` (auto-starts the worker if
needed) and `GET /api/logs` / `GET /api/stats` so the web UI can use one port.

### Health / captions

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Combined manager + worker health |
| GET | `/api/languages` | Available language configs |
| POST | `/api/caption` | Caption inbox (extension pushes here) |
| GET | `/api/captions?since=TS` | Poll captions (web UI pulls) |
| WS  | `/ws/transcribe` | Live audio streaming endpoint (proxied to worker) |

### WebSocket protocol (for the web UI, not the extension)

Client connects to `ws://127.0.0.1:8765/ws/transcribe` and first sends a
JSON config frame:
```json
{"reference_text": "hello world", "live_scoring": true, "target_language": "en"}
```
Server replies with a `ready` frame. Then the client sends binary frames
of 16 kHz mono float32 PCM (250 ms = 4000 samples = 16 KB each).
When done, send the text frame `END` to receive the final scoring JSON
(in the same shape as `/api/assess-speech` plus a `type: "final"` field).

---

## How scoring works (phonetic + fluency + CEFR)

The algorithm is the one from `idea.md`, optimised for low RAM:

1. **Audio decode** — `ffmpeg` decodes the webm/opus (Chrome) or ogg/opus
   (Firefox) recording to 16 kHz mono float32 PCM. Piped I/O, no temp file.

2. **Whisper transcribe** — `faster-whisper` runs the `small` model with
   `int8` quantisation (~500 MB RAM) and `word_timestamps=True`, giving
   per-word text + start/end times + probability.

3. **Word alignment** — the recognised words are aligned against the
   reference text using a Levenshtein-style word-level alignment where
   substitution cost is weighted by phonetic similarity.

4. **Phonetic matching** — each word pair is scored with the **Metaphone**
   algorithm from the `phonetics` library. "colour" / "color" / "kaler"
   all share the Metaphone code `KLR`, so they're treated as the same sound.
   This is what catches mispronunciations that whisper's text decoder
   would otherwise auto-correct.

5. **Per-word verdict** — each reference word becomes:
   - `None` (matched, accuracy = 60% phonetic + 40% whisper confidence)
   - `Mispronunciation` (phonetic code doesn't match — accuracy = 30 max)
   - `Omitted` (skipped entirely — accuracy = 0)
   - `Inserted` (extra spoken word beyond the reference)

6. **Sub-scores**:
   - **accuracy** = mean of matched-word accuracies
   - **completeness** = (matched + mispronounced) / total reference words
   - **fluency** = base 95 / 80 / 65 / 50 by WPM band, minus `pause_penalty × 8`
     for each gap > `pause_threshold_s` between consecutive words
   - **prosody** = rhythm uniformity (1 - stdev/mean of inter-word gaps) × 0.7
     + average whisper confidence × 0.3

7. **Overall** = `0.45·accuracy + 0.20·fluency + 0.15·prosody + 0.20·completeness`
   (the weights from API.md).

8. **CEFR** is derived from overall + WPM:
   - B2+ if overall ≥ 85 and WPM ≥ 110
   - B2 if overall ≥ 70 and WPM ≥ 85
   - B1 if overall ≥ 55 and WPM ≥ 60
   - A2 if overall ≥ 40
   - Below A2 otherwise

The whole pipeline runs inside the worker process. The manager proxies
the result back to the browser or extension.

---

## RAM tuning

All knobs live in `config.py` and can be overridden by env vars without
editing the file.

| Env var | Default | What it does |
|---|---|---|
| `LS_MODEL` | `small` | Whisper model size: `tiny`, `base`, `small`, `medium`, `large-v3` |
| `LS_COMPUTE` | `int8` | `int8` (smallest RAM), `int8_float16`, `float16` |
| `LS_THREADS` | `0` (auto) | CPU threads whisper can use |
| `LS_IDLE_TIMEOUT` | `60` (seconds) | Worker auto-killed after this many seconds of no activity |
| `LS_FREEZE` | `0` | If `1`, manager freezes worker instead of killing (RAM kept, instant resume) |
| `LS_AUTOSTART_WORKER` | `0` | If `1`, manager starts the worker on boot (uses RAM even when idle) |

### Model selection for 8 GB machines

With Arch Linux + browser already using 2.8 GB, you have ~5 GB for the rest.
LanguageShadow stays comfortably inside its 2.3 GB budget on every model up
to `small`. `medium` is doable but you may want `LS_IDLE_TIMEOUT=45` to be
safe. `large-v3` is not recommended on 8 GB machines.

| Model | RAM (CPU int8) | Relative quality | Recommended? |
|---|---|---|---|
| tiny | ~150 MB | low | only for very weak hardware |
| base | ~300 MB | medium | ok for short phrases |
| **small** | ~500 MB | **high (default)** | **best balance** |
| medium | ~1.5 GB | very high | doable, watch idle timeout |
| large-v3 | >3 GB | excellent | not for 8 GB machines |

---

## Languages

Drop a JSON file in `languages/`. See `en.json` for the schema. You get
the language picker in the UI for free.

```json
{
  "name": "English",
  "whisper_lang_code": "en",
  "fluency_ideal_wpm_min": 100,
  "fluency_ideal_wpm_max": 180,
  "coach_tips": {
    "low_accuracy": "...",
    "low_fluency_slow": "...",
    "low_fluency_fast": "...",
    "high_score": "..."
  }
}
```

---

## File layout

```
languageshadow/
├── manager.py          # lightweight controller (port 8765)
├── worker.py            # heavy AI process (port 8000, on-demand)
├── scoring.py           # word-level alignment + pronunciation scoring
├── config.py            # all tunables (env-var overridable)
├── requirements.txt
├── setup.sh             # one-time install + model pre-warm
├── start_manager.sh     # launch manager in background
├── stop_backend.sh      # kill manager + worker
├── languages/           # JSON config per language
│   ├── en.json
│   ├── es.json
│   ├── de.json
│   └── ar.json
├── static/              # served by manager
│   ├── index.html       # home
│   ├── live.html         # live caption page
│   ├── read.html         # read-from-text page
│   ├── youtube.html      # youtube helper page
│   ├── css/style.css
│   └── js/common.js      # shared: recorder, WS client, scoring render
├── extension/           # Manifest V3 browser extension
│   ├── manifest.json
│   ├── background.js
│   ├── content.js        # extracts YouTube captions
│   ├── popup.html
│   ├── popup.js
│   └── icon.svg
└── systemd/
    └── languageshadow-manager.service
```

---

## Troubleshooting

**Microphone not working on Arch / Hyprland** — make sure you're in the
`audio` group and pipewire is running. Test with `pw-record --list-targets`.
If you use pipewire-pulse, the browser permission prompt should just work.

**Worker won't start** — check `logs/languageshadow.log`. Most common cause
is missing model download (run `./setup.sh` again) or another process
holding port 8000 (`ss -ltnp | grep 8000`).

**High RAM usage** — switch to a smaller model: `LS_MODEL=base ./start_manager.sh`.
Also lower `LS_IDLE_TIMEOUT` so the worker dies sooner after you stop speaking.

**Worker never spawns** — make sure the manager is running
(`curl http://127.0.0.1:8765/api/health`). If yes, try `curl -X POST
http://127.0.0.1:8765/manager/start` to force-start it.

---

## License

MIT. See `LICENSE` (your existing ShadowEcho LICENSE carries over).
