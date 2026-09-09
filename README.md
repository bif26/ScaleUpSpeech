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

```bash
cd languageshadow
./setup.sh              # creates .venv, installs deps, pre-downloads model,
                        # builds the Svelte UI (npm install + npm run build)
./start_manager.sh      # starts the manager in the background
# open http://127.0.0.1:8765/ in your browser
```

To stop everything:

```bash
./stop_backend.sh
```

### Prerequisites

- **Python 3.10+** — `sudo pacman -S python` (Arch)
- **ffmpeg** — `sudo pacman -S ffmpeg` (needed for webm/opus decoding)
- **Node.js 18+ + npm** — `sudo pacman -S nodejs npm` (only for the UI build)

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
Use your **existing** Shadowing browser extension — it sends the caption
line it captured and the user's recording to
`POST http://127.0.0.1:8000/api/assess-speech` (per the `API.md` contract).
The server returns the standard scoring JSON and your extension's panel
renders it directly.

The YouTube page also shows the complete API contract inline, plus a live
caption inbox (if your extension posts captions to `/api/caption`, you
can load them as practice reference).

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
├── config.py              # all tunables (env-var overridable)
├── requirements.txt
├── setup.sh               # install venv + build UI
├── start_manager.sh       # launch manager in background
├── stop_backend.sh        # kill manager + worker
├── API.md                 # extension API contract (input/output examples)
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
│   │   │   └── youtube/+page.svelte
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

**Worker won't start** — check `logs/languageshadow.log`. Most common cause
is missing model download (run `./setup.sh` again) or another process
holding port 8000 (`ss -ltnp | grep 8000`).

**UI shows "not built" message** — run `./setup.sh` (or just `cd ui && npm install && npm run build`).

**Extension can't reach the API** — check `curl http://127.0.0.1:8000/health`
returns 200. If not, start the worker: `curl -X POST http://127.0.0.1:8765/manager/start`.

**High RAM usage** — switch to a smaller model: `LS_MODEL=base ./start_manager.sh`.
Also lower `LS_IDLE_TIMEOUT` so the worker dies sooner after you stop speaking.

---

## License

MIT.
