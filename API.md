# LanguageShadow – API Contract

The complete contract your existing **Shadowing** browser extension needs to
talk to LanguageShadow. Implement exactly this and your extension's **Score**
button works out of the box.

This file replaces the previous bundled extension. You already have your
own extension built — keep using it. All you need to know is below.

---

## Endpoint

```
POST http://127.0.0.1:8000/api/assess-speech
Content-Type: application/json
```

The request can be sent by the extension's **background service worker**
(no CORS issues from a content script, and the worker has
`Access-Control-Allow-Origin: *` enabled so even content-script calls work).

---

## Request body

| Field            | Type   | Required | Description |
|------------------|--------|----------|-------------|
| `audio_base64`   | string | yes      | The user's recording. **webm/opus** container (Chrome) or **ogg/opus** (Firefox), base64-encoded. The worker decodes via ffmpeg so any ffmpeg-supported container works (wav, mp3, m4a, webm, ogg). ≤ 30 s recommended. |
| `reference_text` | string | yes      | The caption line the user practised — exactly the subtitle text shown in the panel. |
| `language`       | string | yes      | BCP-47 tag of the caption, e.g. `de-DE`, `en-US`, `ar-AR`. The worker strips the region (`de-DE` → `de`) and passes it to Whisper. Use `auto` for auto-detect. |

The worker also accepts the legacy field name `target_language` instead of
`language` for backwards compatibility — both work.

### Example request

```bash
B64=$(base64 -w0 take.webm)
curl -s http://127.0.0.1:8000/api/assess-speech \
  -H 'Content-Type: application/json' \
  -d "{
    \"audio_base64\": \"$B64\",
    \"reference_text\": \"Ich habe heute einen langen Tag gehabt.\",
    \"language\": \"de-DE\"
  }"
```

### JavaScript example (extension background worker)

```javascript
// Inside your background service worker, when the user clicks "Score":
async function scoreTake(audioBlob, referenceText, language) {
  // audioBlob is a Blob from MediaRecorder (webm/opus on Chrome).
  const arrayBuffer = await audioBlob.arrayBuffer();
  const audioBase64 = btoa(String.fromCharCode(...new Uint8Array(arrayBuffer)));

  const response = await fetch('http://127.0.0.1:8000/api/assess-speech', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      audio_base64: audioBase64,
      reference_text: referenceText,
      language: language,  // BCP-47 tag of the caption
    }),
  });

  if (!response.ok) {
    throw new Error(`Server returned ${response.status}`);
  }
  return await response.json();
}
```

### Manifest V3 host permission

Add this to your extension's `manifest.json` so the background worker can
reach the local server:

```json
{
  "host_permissions": [
    "http://127.0.0.1:8000/*"
  ]
}
```

Firefox users must grant *Access your data for 127.0.0.1* under the
extension's Permissions tab if you don't include the host permission in
the manifest.

---

## Response — the recommended scoring format

Scores are integers **0–100**. Designed for shadowing practice: one honest
overall number, four sub-scores that tell the user WHAT to improve, a pace
readout, and word-level detail for self-correction.

```json
{
  "status": "OK",
  "overall": 84,
  "subscores": {
    "accuracy":     88,
    "fluency":      81,
    "prosody":      76,
    "completeness": 100
  },
  "pace": {
    "wpm": 118,
    "duration_ms": 4200
  },
  "words": [
    { "word": "Ich",   "accuracy": 96, "errorType": "None" },
    { "word": "habe",  "accuracy": 91, "errorType": "None" },
    { "word": "heute", "accuracy": 74, "errorType": "Mispronunciation" },
    { "word": "einen", "accuracy": 0,  "errorType": "Omitted" },
    { "word": "langen","accuracy": 83, "errorType": "None" },
    { "word": "Tag",   "accuracy": 88, "errorType": "None" },
    { "word": "gehabt.","accuracy": 71, "errorType": "Mispronunciation" }
  ],
  "recognized": "ich habe heute langen tag gehabt",

  "cefr": "B2",
  "pause_count": 0,
  "n_matched": 5,
  "n_mispronounced": 2,
  "n_omitted": 1,
  "n_inserted": 0
}
```

### Field reference — what your panel shows where

| Field | Where it shows in your panel |
|-------|------------------------------|
| `overall` (also accepts `overall_score`, `score`, `pronunciation_score`, `pronunciationAssessment.score`) | Big number on the score card + the score pill on the take chip. Saved per take. |
| `subscores.accuracy` / `.fluency` / `.prosody` / `.completeness` (snake or camel; or flat `accuracy_score` etc. at top level) | The 2×2 sub-score grid. |
| `pace.wpm` + `pace.duration_ms` (also accepted flat: `wpm`, `duration_ms`) | The `⏱ words/min · seconds` line. |
| `words[].word` + `words[].accuracy` (or `.score`) | Color-coded word chips: green ≥ 80, yellow ≥ 60, red below. |
| `words[].errorType` | Optional extra hint chip (None / Mispronunciation / Omitted / Inserted). |
| `recognized` | Not shown by default — useful for debugging your ASR. |
| `cefr` | Optional badge showing A2 / B1 / B2 / B2+ level. |
| `pause_count`, `n_matched`, `n_mispronounced`, `n_omitted`, `n_inserted` | Optional diagnostics. |

### What the sub-scores mean

- **accuracy** — how close the pronounced words are to the reference, measured with the Metaphone phonetic algorithm so "colour" / "color" / "kaler" all count as the same sound. Weighted with Whisper's word-level confidence.
- **completeness** — fraction of reference words actually spoken (matched or mispronounced; omitted words lower this).
- **fluency** — rhythm and smoothness: derived from speech rate (WPM) vs an ideal range, minus a pause penalty (gaps > 1.1 s between words subtract 8 pts each).
- **prosody** — intonation / rhythm uniformity: variance of inter-word gaps + average Whisper confidence. Approximate (we don't extract pitch on CPU).

### `errorType` values

| Value | Meaning |
|-------|---------|
| `None` | Word matched the reference (phonetically and textually). |
| `Mispronunciation` | Word sounds different from the reference (Metaphone codes don't match). accuracy ≤ 60. |
| `Omitted` | Reference word was skipped entirely. accuracy = 0. |
| `Inserted` | Extra spoken word beyond the reference (added by the user). accuracy = 0. |

### Overall formula

```
overall = 0.45 * accuracy + 0.20 * fluency + 0.15 * prosody + 0.20 * completeness
```

(0–100, rounded to integer.)

### CEFR level

Derived from overall + WPM:

| Overall | WPM | CEFR |
|---|---|---|
| ≥ 85 | ≥ 110 | B2+ |
| ≥ 70 | ≥ 85  | B2 |
| ≥ 55 | ≥ 60  | B1 |
| ≥ 40 | any   | A2 |
| < 40 | any   | Below A2 |

---

## Error response

If anything goes wrong (audio too short, model not loaded, audio decode
failed), the response is:

```json
{ "status": "ERROR", "error": "short human-readable reason" }
```

The HTTP status code may be 400 (bad request) or 500 (server fault). If the
worker is not running, the connection will be refused — your extension
should show a "score pending" state and let the user press Score again
once the worker is back up.

---

## Health check

Use this to know if the server is up before sending the assess request:

```
GET http://127.0.0.1:8000/health
```

```json
{
  "status": "ok",
  "pid": 12345,
  "rss_mb": 540,
  "model_loaded": true,
  "model_name": "small",
  "ffmpeg_available": true,
  "uptime_s": 12.4
}
```

`model_loaded: false` on first call is normal — the first assess request
will lazy-load the model (takes 2–4 s) and then proceed. Subsequent
requests are fast.

---

## Other endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Worker health + RAM + model status |
| GET | `/api/logs?limit=N` | Recent takes (newest last) |
| GET | `/api/stats` | Aggregate stats (avg_overall, total_assessments, etc.) |
| POST | `/heartbeat` | Reserved for the manager's heartbeat |

### Logs example

```
GET http://127.0.0.1:8000/api/logs?limit=3
```

```json
{
  "logs": [
    {
      "ts": 1737000000.5,
      "reference": "Ich habe heute einen langen Tag gehabt.",
      "language": "de-DE",
      "status": "OK",
      "overall": 84,
      "subscores": { "accuracy": 88, "fluency": 81, "prosody": 76, "completeness": 100 },
      "pace": { "wpm": 118, "duration_ms": 4200 },
      "words": [...],
      "recognized": "ich habe heute langen tag gehabt",
      "cefr": "B2"
    }
  ]
}
```

---

## Manager port (optional)

If your extension wants to talk to the manager instead (port 8765), the
same `POST /api/assess-speech` endpoint is proxied there. The manager
auto-starts the worker if it's stopped. CORS is enabled on both ports.

```
POST http://127.0.0.1:8765/api/assess-speech
```

Identical request/response shape. Useful if you want one firewall rule /
one port for everything (browser + extension + status pings).

---

## Server-side recipe (how these scores are produced)

For reference — you don't need to implement this, it's already done:

1. **Decode** `audio_base64` → webm/opus bytes → ffmpeg pipe → 16 kHz mono
   float32 PCM numpy array. No temp file, piped I/O, minimal RAM.
2. **ASR** the audio in the requested `language` using `faster-whisper`
   with `word_timestamps=True` for per-word text + start/end + probability.
3. **Align** the recognized words against `reference_text` using
   word-level Levenshtein alignment where substitution cost is weighted
   by phonetic similarity.
4. **Phonetic matching** with the `phonetics` library's **Metaphone**
   algorithm: "colour" / "color" / "kaler" all share the Metaphone code
   `KLR`, so they count as the same sound. This catches mispronunciations
   that Whisper's text decoder would otherwise auto-correct.
5. **Per-word verdict**: `None` (matched), `Mispronunciation` (phonetic
   code doesn't match), `Omitted` (skipped), `Inserted` (extra word).
6. **Sub-scores**:
   - accuracy = mean of matched-word accuracies (60% phonetic + 40% Whisper confidence)
   - completeness = (matched + mispronounced) / total reference words
   - fluency = base 95/80/65/50 by WPM band, minus 8 × number of pauses > 1.1 s
   - prosody = rhythm uniformity × 0.7 + average Whisper confidence × 0.3
7. **overall** = `0.45·accuracy + 0.20·fluency + 0.15·prosody + 0.20·completeness`
8. **CEFR** level derived from overall + WPM.

---

## Troubleshooting

**`ECONNREFUSED` when the extension calls the endpoint** — the worker
isn't running. Start it:
```bash
cd languageshadow && ./start_manager.sh
```
The manager auto-starts the worker on the first assess request, so you
can also just send the request and wait ~3 s for the cold start.

**`{"status": "ERROR", "error": "audio decode failed: ..."}`** — the
audio format isn't recognised by ffmpeg. Make sure your MediaRecorder
uses `audio/webm;codecs=opus` (Chrome) or `audio/ogg;codecs=opus` (Firefox).

**`{"status": "ERROR", "error": "Audio too short (<0.1s)"}`** — the
recording is empty. Increase the min recording length in your extension.

**`{"status": "ERROR", "error": "model load failed: ..."}` — faster-whisper
isn't installed. Run `./setup.sh` again to install dependencies and
pre-download the Whisper model.

**Scores are all 0** — the language tag doesn't match what was spoken.
Try `auto` to let Whisper detect.

---

## CORS

The worker has `Access-Control-Allow-Origin: *` enabled, so calls from
any origin work. Preflight (`OPTIONS`) responses include the standard
`Access-Control-Allow-Methods` and `Access-Control-Allow-Headers` headers.
