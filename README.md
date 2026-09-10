# LanguageShadow

Privacy-first pronunciation coach that runs **entirely on your machine**.
Listens to your microphone, transcribes with Whisper, and scores your
pronunciation word-by-word against any reference text — like a typing-test
site, but for speaking.

Built for **Arch Linux + Hyprland** on an 8 GB machine that already uses
~2.8 GB for OS + browser. The whole app fits inside a 2–2.3 GB RAM budget.

> The web UI is built with **SvelteKit + shadcn-svelte + Catppuccin Mocha**.
> The browser extension is **not bundled** — you bring your own Shadowing
> extension and link it to the local API. See `API.md` for the complete
> contract.

---

## Two-process architecture

```
+---------------------------+                  +---------------------------+
|  Manager (port 8765)      |  spawn / kill    |  Worker (port 8000)       |
|  - Svelte UI (built SPA)  | ---------------->|  - faster-whisper model   |
|  - REST control API       |  heartbeat       |  - phonetic scoring       |
|  - WS proxy to worker     | <----------------|  - webm/opus decode       |
|  - auto idle-kill (60 s)  |                  |  - on-demand, RAM-heavy   |
|  ~30-50 MB, always on     |                  |  ~150-700 MB, on demand  |
+---------------------------+                  +---------------------------+
        ^                                                ^
        |                                                |
        | browser talks only to port 8765                | spawned as subprocess
        | (one firewall rule, one CORS origin)           | of manager.py
        |                                                |
   +----+----+                                      +----+----+
   | browser | (Svelte UI + mic via AudioWorklet)    | your   |
   +---------+                                       | existing|
                                                    | Shadowing|
                                                    | extension|
                                                    +----------+
```

| Process | Port | RAM (typical) | When alive |
|---|---|---|---|
| Manager | 8765 | 30-50 MB | always (boot) |
| Worker  | 8000 | 150-700 MB | on-demand, auto-killed after 60 s idle |

### Why two ports?

- **RAM budget**: the heavy Whisper model only lives when you're speaking. The
  manager stays tiny so the rest of your system has RAM to breathe.
- **Single browser surface**: the browser only talks to port 8765. The
  manager proxies the live WebSocket to the worker.
- **Lifecycle control**: start / stop / **freeze / thaw** the AI process
  from the UI. Freeze keeps the model warm but stops it using CPU when you
  just pause for a few minutes.
- **Extension friendly**: the worker (port 8000) has CORS enabled and speaks
  the exact API your existing Shadowing extension expects (see `API.md`).

---

## Quick start

### First time only — full setup

The model download is a **separate step** so a slow-internet user is never
blocked by a ~500 MB download during initial setup:

```bash
cd ScaleUpSpeech
./setup.sh                       # 1. venv + Python deps + Svelte UI build (no model)
python3 download_model.py       # 2. ONE-TIME Whisper model download (visible progress bars)
./start_manager.sh              # 3. launch the manager
# open http://127.0.0.1:8765/ in your browser
```

`./setup.sh --with-model` also works if you want the model download bundled
into setup (the old behaviour — useful for headless / CI installs).

### Every other time — just start it

```bash
./start_manager.sh      # instant: reuses the existing .venv + cached model
# open http://127.0.0.1:8765/ in your browser
```

`start_manager.sh` is self-healing: if (and only if) the venv or some
dependency is missing, it calls `./setup.sh` for you — otherwise it starts
immediately. It also probes the model cache and warns loudly if the model
is missing, so you know to run `python3 download_model.py` before speaking.
Nothing is re-downloaded on subsequent runs:

- Python deps live in `.venv/` and are only touched when `requirements.txt`
  imports fail (first run, or after they change).
- The Whisper model is downloaded **exactly once**, with a visible progress
  bar (`python3 download_model.py` does the same thing manually), into
  `~/.cache/huggingface/` — **outside the repo**. `git pull`, `rm -rf .venv`,
  even a fresh re-clone never re-download it. The worker also loads the
  model straight from disk (offline-fast, no hub checks) once cached.
- The Svelte UI is rebuilt only when UI sources changed since the last build.

### One-off model commands

```bash
python3 download_model.py            # download default model (small)
python3 download_model.py tiny        # override model just for this run
python3 download_model.py --status    # check if cached (no download, exits 0/1)
```

`download_model.py` is self-bootstrapping: if `huggingface_hub` / `tqdm` are
missing it installs them on demand, so it works on a bare system Python too
(no need to activate the venv first).

To stop everything:

```bash
./stop_backend.sh
```

### Prerequisites

- **Python 3.10+** — `sudo pacman -S python` (Arch). Python 3.14 works.
- **ffmpeg** — `sudo pacman -S ffmpeg` (needed for webm/opus decoding)
- **Node.js 18+ + npm** — `sudo pacman -S nodejs npm` (only for the UI build)

> **Python 3.14 (Arch) note:** `requirements.txt` uses version floors instead
> of hard pins, so pip automatically picks releases with prebuilt cp314
> wheels. `uvloop` is optional (the worker falls back to the stdlib asyncio
> loop) and is skipped on Pythons it does not support yet.

### Auto-start on boot (systemd)

```bash
# 1. Edit the service file and replace /path/to/languageshadow with the real path:
$EDITOR systemd/languageshadow-manager.service

# 2. Install as a user service (so it runs as your user, not root — needed
#    for microphone permissions and Hyprland display access):
sudo cp systemd/languageshadow-manager.service /etc/systemd/system/
systemctl --user daemon-reload
systemctl --user enable --now languageshadow-manager
```

---

## The five pages

### 1. `/live` — Live Caption
Free speech mode: click **Start captioning** and just talk — Whisper
transcribes every word in real time, no reference text, no scoring.
The AI worker auto-starts with the page, so the model loads while you
grant mic permission.

What you get:
- live transcript — finished text in white, the in-progress phrase pulsing in lavender
- running word count, timer and live WPM
- **Copy**, **Save .txt** and **Clear** buttons for the transcript
- language selector (EN / ES / DE / FR / AR / auto-detect)
- the worker keeps only a rolling audio tail, so latency and RAM stay flat
  even in hours-long caption sessions

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

### 3. `/exam` — Exam Trainer (German A2–B2)
CEFR speaking practice in the real **Goethe / ÖSD / telc** exam format:

1. **Pick a level and a task** — tasks live in `tasks/A2/`, `tasks/B1/`,
   `tasks/B2/` as plain Markdown files with YAML frontmatter. Adding a task
   = dropping a file in the folder (see `tasks/README.md`); it appears in
   the UI immediately, no restart.
2. **Prep** — the situation and the required points are shown, with a
   countdown (`prep_seconds`).
3. **Speak** — recording starts automatically after prep (or press
   "Start speaking now"); you see a live transcript preview and the
   remaining time; recording auto-stops after `speak_seconds`, exactly like
   the real exam cuts you off.
4. **Result** — full transcript, fluency metrics (words, net WPM, pauses
   > 0.5 s, longest pause, filled pauses like äh/ähm) and every word the
   recogniser was unsure about.
5. **Export assessment file** — one self-contained Markdown file with the
   task, the measured performance, the word-level pronunciation evidence
   and the complete official-style rubric (5 criteria: Task Fulfillment 30 %,
   Coherence 15 %, Vocabulary 20 %, Grammar 20 %, Pronunciation 15 %).
   Copy it into **any LLM** — ChatGPT, Claude, Gemini or a local model —
   and it acts as a German CEFR examiner: score out of 100, CEFR level,
   pass/borderline/fail, per-criterion feedback and a German summary.
   No API key, no account, no subscription — the rubric travels inside the file.

The transcript runs through the same worker WebSocket as the other pages
(with `mode: "exam"`), so the model stays warm and results stay fast.

### 4. `/youtube` — YouTube Helper
Use your **existing** Shadowing browser extension — it sends the caption
line it captured and the user's recording to
`POST http://127.0.0.1:8000/api/assess-speech` (per the `API.md` contract).
The server returns the standard scoring JSON and your extension's panel
renders it directly.

The YouTube page also shows the complete API contract inline, plus a live
caption inbox (if your extension posts captions to `/api/caption`, you
can load them as practice reference).

### 5. `/logs` — Logs
Everything the stack prints, in one place:

- **Aggregate stats** — total assessments, audio seconds, running averages
  (from the worker's in-memory store; only when the worker is running).
- **System log** — the runtime log of manager + worker, parsed from
  `logs/languageshadow.log` with level colours (red = error, yellow =
  warning). Filter by source (all / manager / worker / system), auto-refresh
  every 3 s (pausable). Read straight from disk, so it works even while the
  worker is stopped — exactly what you need when debugging a worker that
  won't start.
- **Worker stdout** — the raw stdout/stderr of the worker captured to
  `logs/worker.out`. This is where startup crashes land (import errors,
  missing model files, …) — previously that output went to /dev/null and
  a dead worker looked like a mystery.
- **Recent assessments** — every scored attempt in a compact table
  (time, language, overall / accuracy / fluency / reference snippet).
  Resets when the worker is idle-killed.

---

## For extension developers

The complete API contract lives in **`API.md`** at the repo root.

In short:

- `POST http://127.0.0.1:8000/api/assess-speech`
- Request body: `{ audio_base64, reference_text, language }` (webm/opus base64)
- Response: `{ status, overall, subscores: { accuracy, fluency, prosody, completeness }, pace: { wpm, duration_ms }, words: [{ word, accuracy, errorType }], recognized, cefr, ... }`
- CORS enabled for all origins.
- Manifest V3 host permission: `"host_permissions": ["http://127.0.0.1:8000/*"]`

See `API.md` for the full request/response examples, error format, and
server-side recipe.

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
| `LS_AUTOSTART_WORKER` | `0` | If `1`, manager starts the worker on boot |

### Model selection for 8 GB machines

With Arch + browser already using 2.8 GB, you have ~5 GB for the rest.
LanguageShadow stays comfortably inside its 2.3 GB budget on every model up
to `small`. `medium` is doable but you may want `LS_IDLE_TIMEOUT=45`.

| Model | RAM (CPU int8) | Relative quality | Recommended? |
|---|---|---|---|
| tiny | ~150 MB | low | only for very weak hardware |
| base | ~300 MB | medium | ok for short phrases |
| **small** | ~500 MB | **high (default)** | **best balance** |
| medium | ~1.5 GB | very high | doable, watch idle timeout |
| large-v3 | >3 GB | excellent | not for 8 GB machines |

---

## How scoring works (phonetic + fluency + CEFR)

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
   This catches mispronunciations that whisper's text decoder would
   otherwise auto-correct.

5. **Per-word verdict** — each reference word becomes:
   - `None` (matched, accuracy = 60% phonetic + 40% whisper confidence)
   - `Mispronunciation` (phonetic code doesn't match — accuracy ≤ 60)
   - `Omitted` (skipped entirely — accuracy = 0)
   - `Inserted` (extra spoken word beyond the reference)

6. **Sub-scores**:
   - **accuracy** = mean of matched-word accuracies
   - **completeness** = (matched + mispronounced) / total reference words
   - **fluency** = base 95 / 80 / 65 / 50 by WPM band, minus `8 × number of pauses > pause_threshold_s`
   - **prosody** = rhythm uniformity (1 - stdev/mean of inter-word gaps) × 0.7
     + average whisper confidence × 0.3

7. **Overall** = `0.45·accuracy + 0.20·fluency + 0.15·prosody + 0.20·completeness`

8. **CEFR** derived from overall + WPM:
   - B2+ if overall ≥ 85 and WPM ≥ 110
   - B2 if overall ≥ 70 and WPM ≥ 85
   - B1 if overall ≥ 55 and WPM ≥ 60
   - A2 if overall ≥ 40
   - Below A2 otherwise

---

## Languages

Drop a JSON file in `languages/`. See `en.json` for the schema. The UI
language picker picks them up automatically.

```json
{
  "name": "English",
  "whisper_lang_code": "en",
  "fluency_ideal_wpm_min": 100,
  "fluency_ideal_wpm_max": 180,
  "coach_tips": { ... }
}
```

---

## File layout

```
languageshadow/
├── manager.py             # lightweight controller (port 8765)
├── worker.py              # heavy AI process (port 8000, on-demand)
├── scoring.py             # Metaphone scoring + CEFR + pause penalty
├── exam.py                # Exam page: task parser, speech metrics, CEFR MD export
├── config.py              # all tunables (env-var overridable)
├── requirements.txt
├── setup.sh               # install venv + build UI
├── download_model.py      # one-time Whisper model download w/ visible progress
├── start_manager.sh       # launch manager in background
├── stop_backend.sh        # kill manager + worker
├── API.md                 # extension API contract (input/output examples)
├── tasks/                 # Exam task library (drop a .md file = new task)
│   ├── README.md          # task file form + field reference
│   ├── A2/  B1/  B2/      # one .md file per exam task (Goethe/ÖSD/telc format)
├── languages/             # JSON config per language
│   ├── en.json
│   ├── es.json
│   ├── de.json
│   └── ar.json
├── ui/                    # SvelteKit + shadcn-svelte + Catppuccin Mocha
│   ├── package.json
│   ├── svelte.config.js
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── src/
│   │   ├── app.html
│   │   ├── app.css        # Catppuccin Mocha palette as Tailwind v4 theme
│   │   ├── routes/
│   │   │   ├── +layout.svelte  # sidebar + topbar shell
│   │   │   ├── +page.svelte    # Home
│   │   │   ├── live/+page.svelte
│   │   │   ├── read/+page.svelte
│   │   │   ├── exam/+page.svelte   # Exam Trainer (German A2–B2)
│   │   │   ├── youtube/+page.svelte
│   │   │   └── logs/+page.svelte   # system logs + worker stdout + practice history
│   │   └── lib/
│   │       ├── api.ts           # REST client (port 8765)
│   │       ├── recorder.ts      # AudioWorklet recorder + WS session
│   │       ├── types.ts         # shared API types
│   │       ├── utils.ts         # cn() class merge
│   │       ├── stores/health.ts # polling health store
│   │       └── components/ui/  # shadcn-svelte style components
│   │           ├── button/
│   │           ├── card/
│   │           ├── slider/
│   │           ├── badge/
│   │           ├── select/
│   │           ├── textarea/
│   │           ├── input/
│   │           ├── separator/
│   │           └── toast/
│   ├── static/
│   │   └── favicon.svg
│   └── (build/)           # generated by `npm run build`
└── systemd/
    └── languageshadow-manager.service
```

---

## Troubleshooting

**Microphone not working on Arch / Hyprland** — make sure you're in the
`audio` group and pipewire is running. Test with `pw-record --list-targets`.

**Worker won't start** — check `logs/languageshadow.log` (or the `/logs`
page in the UI). Most common cause is a missing model download. Run
`python3 download_model.py --status` to check; if not cached, run
`python3 download_model.py` to fetch it. Another common cause is another
process holding port 8000 (`ss -ltnp | grep 8000`).

**`ModuleNotFoundError: No module named 'huggingface_hub'`** — fixed.
`download_model.py` now self-installs `huggingface_hub` + `tqdm` on demand,
and they are also pinned in `requirements.txt` so a normal `./setup.sh`
install already has them. Just `git pull` and re-run
`python3 download_model.py`.

**WebSocket / Live Caption: `finalize transcribe failed: model not loaded`** —
fixed. The WS path now loads the model before sending `ready`, so a cold
worker start no longer hits this on the first chunk. If you still see it,
run `python3 download_model.py` — the model is probably not cached and the
network is unavailable.

**Model download shows no progress / I want to check the cache** — run
`python3 download_model.py --status` (just probes the cache, no download) or
`python3 download_model.py` (prints file sizes, shows progress bars, and
skips instantly — 0 MB — when the model is already cached). The cache lives
in `~/.cache/huggingface/hub/models--Systran--faster-whisper-*`. To force a
re-download of the current model, delete that folder.

**`TypeError: incompatible constructor arguments` from ctranslate2** — fixed
in this version (it was `cpu_threads=None` being passed when `LS_THREADS=0`).
`git pull` to get the fix; no other action needed.

**`RuntimeError: [json.exception.parse_error.101] parse error ... unexpected
end of input` when the model loads** — your local HF cache contains a
**0-byte leftover file** (typically an empty `vocabulary.json` or
`preprocessor_config.json`). These files do not exist in the upstream
`Systran/faster-whisper-*` repos at all; an interrupted or manual download
left the empty placeholders behind, and `huggingface_hub` never removes them
(because they are not in the upstream file list, re-downloads skip them).
CTranslate2 prefers `vocabulary.json` over the perfectly valid
`vocabulary.txt` next to it, so an empty phantom kills the model load.
Fixed to self-heal: the worker now detects the failure, deletes 0-byte
cache files, and retries — a full clean re-download is the last resort.
You can also repair manually: `python3 download_model.py --repair`, or
delete the empty files yourself:
`find ~/.cache/huggingface/hub/models--Systran--faster-whisper-*/snapshots -size 0 -type f -delete`

**pip fails to build a wheel on a brand-new Python** — you likely have an old
checkout with pinned versions. `git pull` and delete the stale venv so it is
recreated with the relaxed pins: `rm -rf .venv && ./setup.sh`.

**UI shows "not built" message or `/logs` returns 404** — run `./setup.sh`
(or just `cd ui && npm install && npm run build`). The `/logs` route was
added in this version; if your `ui/build/` is stale you'll see a 404.

**Extension can't reach the API** — check `curl http://127.0.0.1:8000/health`
returns 200. If not, start the worker: `curl -X POST http://127.0.0.1:8765/manager/start`.

**High RAM usage** — switch to a smaller model: `LS_MODEL=base ./start_manager.sh`.
Also lower `LS_IDLE_TIMEOUT` so the worker dies sooner after you stop speaking.

---

## License

MIT.
