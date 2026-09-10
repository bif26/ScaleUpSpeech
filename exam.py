"""
LanguageShadow - CEFR Exam Trainer module

Everything the "Exam" page needs on the backend, in ONE pure-stdlib module
(only optional import: PyYAML, with a built-in fallback parser). Safe to
import from BOTH the manager (lightweight, always-on) and the worker.

Three responsibilities:

  1. Task library      - parse tasks/<LEVEL>/*.md (and *.json) files with a
                         small YAML frontmatter subset. Adding a task =
                         dropping a file in a folder. No code changes.
  2. Speech metrics    - WPM, pauses, filled pauses from whisper's
                         word-level output (pure python, no numpy).
  3. Assessment export - build the ONE self-contained Markdown file the
                         learner pastes into any LLM (ChatGPT/Claude/Gemini)
                         to get an official-style CEFR speaking assessment.

Design notes:
  * The MD file follows the project discussion spec: 5 criteria
    (Task Fulfillment 30 / Coherence 15 / Vocabulary 20 / Grammar 20 /
    Pronunciation 15), score /100, level mapping, pronunciation scored from
    an ASR-confidence evidence table (the LLM never sees audio).
  * Everything the LLM must obey is written INTO the file, so the result is
    consistent across LLMs and runs.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
LEVELS = ("A2", "B1", "B2")

# Gaps between consecutive words longer than this are counted as pauses
# (the project spec: "Pauses > 0.5 s").
PAUSE_THRESHOLD_S = 0.5

# Filled pauses (German). "also" is included on purpose (the project spec
# lists it); the exported file notes that it can also be a normal
# conjunction, so the LLM treats it as weak evidence.
FILLER_WORDS = (
    "äh", "ähm", "ähmm", "ähän",
    "öh", "öhm",
    "ehm", "ehmm", "äm",
    "ah", "ahm", "aehm",
    "hm", "hmm", "hmmm", "mhm",
    "also",
)

# Accuracy buckets used by the pronunciation rubric (project spec 6.3).
ACC_GOOD = 0.90
ACC_LOW = 0.70

_TASK_LINE_RE = re.compile(r"^([A-Za-z0-9_]+):\s*(.*)$")
_LIST_ITEM_RE = re.compile(r"^\s+-\s?(.*)$")


# ---------------------------------------------------------------------------
# 1. Task library
# ---------------------------------------------------------------------------
def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1]
    return v


def _coerce_scalar(v: str) -> Any:
    v = _unquote(v)
    if re.fullmatch(r"-?\d+", v):
        try:
            return int(v)
        except ValueError:
            return v
    return v


def _parse_frontmatter_minimal(raw: str) -> Dict[str, Any]:
    """Parse the documented frontmatter subset WITHOUT PyYAML.

    Supported (this is exactly what tasks/README.md documents):
      key: value            -> str (or int when all digits)
      key: "value"          -> str, quotes stripped
      key: |                -> literal block scalar (indented lines)
      key:                  -> list of "  - item" lines below
    Unknown lines are ignored; malformed files never crash the manager.
    """
    out: Dict[str, Any] = {}
    current_key: Optional[str] = None
    block_lines: List[str] = []
    in_block = False
    block_indent = 0

    for line in raw.splitlines():
        if in_block:
            stripped = line.strip()
            if not stripped:
                # keep blank lines inside blocks (trimmed later)
                block_lines.append("")
                continue
            indent = len(line) - len(line.lstrip(" "))
            if indent >= max(block_indent, 2):
                block_lines.append(line[block_indent:] if len(line) >= block_indent else stripped)
                continue
            # dedent ended -> flush block
            out[current_key] = "\n".join(block_lines).strip()
            in_block = False
            block_lines = []
            current_key = None

        m = _TASK_LINE_RE.match(line)
        if m and not line.startswith((" ", "\t")):
            key, val = m.group(1), m.group(2).strip()
            if val == "|":
                in_block = True
                block_indent = 2
                block_lines = []
                current_key = key
                out[key] = ""
            elif val == "":
                # either a list follows, or an empty value
                current_key = key
                out[key] = []
            else:
                out[key] = _coerce_scalar(val)
                current_key = None
            continue

        m = _LIST_ITEM_RE.match(line)
        if m and current_key is not None:
            if isinstance(out.get(current_key), list):
                out[current_key].append(_unquote(m.group(1)))
            continue

        # comment / unknown line -> ignore
    if in_block and current_key is not None:
        out[current_key] = "\n".join(block_lines).strip()
    return out


def _parse_frontmatter(raw: str) -> Dict[str, Any]:
    """PyYAML when available (bullet-proof), fallback mini parser otherwise."""
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return _parse_frontmatter_minimal(raw)


def _split_frontmatter(text: str) -> tuple:
    """Return (frontmatter_str, body_str). Missing delimiters -> ('', text)."""
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return "", text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1:])
    return "", text


def parse_task_file(path: Path, level_hint: str = "") -> Optional[Dict[str, Any]]:
    """Parse one task file into a normalized task dict (or None if invalid)."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None

    slug = path.stem
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except Exception:
            return None
        body = str(data.pop("description", "") or data.pop("body", "") or "")
        fm = data
    else:
        fm_raw, body = _split_frontmatter(text)
        if not fm_raw:
            return None
        fm = _parse_frontmatter(fm_raw)

    level = str(fm.get("level") or level_hint or "").upper()
    if level_hint:
        level = level_hint  # folder wins -> moving a file re-levels it
    if level not in LEVELS:
        return None

    title = ""
    for line in body.splitlines():
        if line.strip().startswith("#"):
            title = line.strip().lstrip("#").strip()
            break
    if not title:
        title = str(fm.get("task_type") or slug).replace("_", " ")

    description = ""
    # body minus its heading line (body may start with blank lines)
    if title:
        description = re.sub(r"^\s*#\s+[^\n]*\n?", "", body, count=1).strip()

    try:
        prep = int(fm.get("prep_seconds", 60) or 60)
        speak = int(fm.get("speak_seconds", 120) or 120)
    except (TypeError, ValueError):
        prep, speak = 60, 120

    req = fm.get("requirements") or []
    if isinstance(req, str):
        req = [r.strip() for r in req.splitlines() if r.strip()]

    situation = str(fm.get("situation", "") or "").strip()

    return {
        "level": level,
        "slug": slug,
        "file": path.name,
        "title": title,
        "exam": str(fm.get("exam", "") or "").strip(),
        "exam_source": str(fm.get("exam_source", "") or "").strip(),
        "task_type": str(fm.get("task_type", "") or "").strip(),
        "situation": situation,
        "requirements": [str(r) for r in req],
        "prep_seconds": max(0, prep),
        "speak_seconds": max(10, speak),
        "language": str(fm.get("language", "de-DE") or "de-DE"),
        "description": description,
    }


def tasks_root(base_dir: Optional[Path] = None) -> Path:
    root = Path(base_dir) if base_dir else Path(__file__).resolve().parent
    return root / "tasks"


def list_tasks(base_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Scan tasks/<LEVEL>/*.{md,json} -> sorted task list (folder = level)."""
    root = tasks_root(base_dir)
    out: List[Dict[str, Any]] = []
    if not root.is_dir():
        return out
    for level in LEVELS:
        ldir = root / level
        if not ldir.is_dir():
            continue
        for f in sorted(ldir.iterdir()):
            if f.suffix.lower() not in (".md", ".json") or not f.is_file():
                continue
            t = parse_task_file(f, level_hint=level)
            if t:
                out.append(t)
    return out


def find_task(level: str, slug: str, base_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    level = (level or "").upper()
    if level not in LEVELS or not slug:
        return None
    root = tasks_root(base_dir)
    # slug is a file stem; never allow path traversal
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", slug):
        return None
    for ext in (".md", ".json"):
        f = root / level / f"{slug}{ext}"
        if f.is_file():
            return parse_task_file(f, level_hint=level)
    return None


# ---------------------------------------------------------------------------
# 2. Speech metrics (pure python, works on whisper word dicts)
# ---------------------------------------------------------------------------
# Punctuation stripped when comparing tokens (quotes, German quotes, dashes).
_STRIP_CHARS = '.,!?;:"\'()[]{}\u00ab\u00bb\u201e\u201c\u201d\u2019\u2014\u2013-'


def _clean_token(w: str) -> str:
    return (w or "").strip().lower().strip(_STRIP_CHARS)


def compute_speech_metrics(words: List[dict],
                           duration_s: Optional[float] = None,
                           pause_threshold_s: float = PAUSE_THRESHOLD_S) -> Dict[str, Any]:
    """Fluency metrics from word-level whisper output.

    Accepts words as dicts with {"word","start","end"} (probability optional).
    """
    toks: List[tuple] = []
    for w in words or []:
        token = _clean_token(w.get("word", ""))
        if not token:
            continue
        try:
            start = float(w.get("start", 0.0))
            end = float(w.get("end", start))
        except (TypeError, ValueError):
            start = end = 0.0
        toks.append((token, start, max(end, start)))

    n = len(toks)
    if not n:
        return {
            "word_count": 0, "duration_s": round(duration_s or 0.0, 2),
            "speech_duration_s": 0.0, "wpm": 0, "wpm_net": 0,
            "pause_count": 0, "pause_total_s": 0.0, "longest_pause_s": 0.0,
            "pauses": [], "filler_count": 0, "fillers": {},
        }

    first_start = min(t[1] for t in toks)
    last_end = max(t[2] for t in toks)
    span = max(last_end - first_start, 0.001)

    pauses = []
    pause_total = 0.0
    longest = 0.0
    for i in range(1, n):
        gap = toks[i][1] - toks[i - 1][2]
        if gap > pause_threshold_s:
            pauses.append({
                "after_word": toks[i - 1][0],
                "at_s": round(toks[i - 1][2], 2),
                "duration_s": round(gap, 2),
            })
            pause_total += gap
            longest = max(longest, gap)

    fillers: Dict[str, int] = {}
    for token, _s, _e in toks:
        if token in FILLER_WORDS:
            fillers[token] = fillers.get(token, 0) + 1

    wpm = int(round(n / (span / 60.0)))
    net = max(span - pause_total, 0.001)
    wpm_net = int(round(n / (net / 60.0)))

    return {
        "word_count": n,
        "duration_s": round(duration_s, 2) if duration_s else round(span, 2),
        "speech_duration_s": round(span, 2),
        "wpm": wpm,
        "wpm_net": wpm_net,
        "pause_count": len(pauses),
        "pause_total_s": round(pause_total, 2),
        "longest_pause_s": round(longest, 2),
        "pauses": pauses[:60],  # cap: a pathological 300-pause answer is useless verbatim
        "filler_count": sum(fillers.values()),
        "fillers": fillers,
    }


def word_evidence(words: List[dict]) -> Dict[str, Any]:
    """Per-word pronunciation evidence from whisper confidence.

    accuracy := whisper's word probability (0..1). Honest about what this is:
    it is how confidently the ASR recognised the word, which correlates with
    intelligibility but is NOT a phonetic score.
    error_type buckets follow the rubric thresholds:
      >= 0.90 ok | 0.70-0.89 unclear | < 0.70 mispronounced
    """
    out: List[Dict[str, Any]] = []
    for i, w in enumerate(words or [], start=1):
        token = (w.get("word") or "").strip().strip(_STRIP_CHARS)
        if not token:
            continue
        try:
            p = float(w.get("probability", 0.0))
        except (TypeError, ValueError):
            p = 0.0
        p = max(0.0, min(1.0, p))
        if p >= ACC_GOOD:
            et = "ok"
        elif p >= ACC_LOW:
            et = "unclear"
        else:
            et = "mispronounced"
        item = {
            "index": len(out) + 1,
            "word": token,
            "start": round(float(w.get("start", 0.0) or 0.0), 2),
            "end": round(float(w.get("end", 0.0) or 0.0), 2),
            "accuracy": round(p, 2),
            "error_type": et,
        }
        if et != "ok":
            note = []
            if re.search(r"[äöüßÄÖÜ]", token):
                note.append("contains umlaut/ß - check vowel quality (ä/ö/ü, ei/ie)")
            if re.search(r"(?:e|er|en)$", token) and len(token) > 3:
                note.append("word ending -e/-er/-en may be swallowed")
            item["note"] = "; ".join(note) if note else "low ASR confidence"
        out.append(item)

    n = len(out)
    n_ok = sum(1 for w in out if w["error_type"] == "ok")
    n_unclear = sum(1 for w in out if w["error_type"] == "unclear")
    n_bad = sum(1 for w in out if w["error_type"] == "mispronounced")
    lowest = sorted(out, key=lambda w: w["accuracy"])[:10]
    return {
        "words": out,
        "summary": {
            "total_words": n,
            "ok_words": n_ok,
            "ok_pct": round(100 * n_ok / n, 1) if n else 0.0,
            "unclear_words": n_unclear,
            "unclear_pct": round(100 * n_unclear / n, 1) if n else 0.0,
            "mispronounced_words": n_bad,
            "mispronounced_pct": round(100 * n_bad / n, 1) if n else 0.0,
            "umlaut_flagged": sum(1 for w in out if w["error_type"] != "ok"
                                  and re.search(r"[äöüßÄÖÜ]", w["word"])),
            "lowest_words": [w["word"] for w in lowest if w["error_type"] != "ok"],
        },
    }


# ---------------------------------------------------------------------------
# 3. Assessment export (the one self-contained MD file for any LLM)
# ---------------------------------------------------------------------------
_CRITERIA = [
    ("task_fulfillment", "Task Fulfillment", 30,
     "Did the learner address ALL required points of the task? Quote transcript evidence per requirement."),
    ("coherence", "Coherence", 15,
     "Are ideas logically connected (connectives, structure, reference devices)? Does the answer hold together?"),
    ("vocabulary", "Vocabulary", 20,
     "Range and appropriateness of words for the level; topic-specific vocabulary; avoidance strategies; repetition."),
    ("grammar", "Grammar", 20,
     "Accuracy AND complexity of structures (word order, cases, verb forms, subordinate clauses) appropriate to the level."),
    ("pronunciation", "Pronunciation", 15,
     "Scored ONLY from the Pronunciation Evidence table in Section 4 - you do NOT hear audio."),
]

_LEVEL_EXPECTATIONS = {
    "A2": "A2 (CEFR can-do): can describe everyday topics (family, shopping, work, routine) in simple linked sentences; can manage short, predictable exchanges. Errors are expected; the message must survive them.",
    "B1": "B1 (CEFR can-do): can deal with most situations while travelling; can give connected reasons, plans, opinions and descriptions; can narrate a story or the plot of a book/film. Noticeable errors and pauses are normal, but speech stays understandable without strain.",
    "B2": "B2 (CEFR can-do): can give clear, detailed accounts and argue a case with advantages/disadvantages; can sustain interaction naturally, use flexible vocabulary and mostly accurate complex structures; mistakes are rare and rarely cause misunderstanding.",
}

_JSON_OUTPUT_EXAMPLE = """{
  "overall_score": 78,
  "overall_level": "B1",
  "exam_result": "PASS",
  "criteria": {
    "task_fulfillment": {"score": 4, "points": 24, "comment": "..."},
    "coherence":        {"score": 4, "points": 12, "comment": "..."},
    "vocabulary":       {"score": 4, "points": 16, "comment": "..."},
    "grammar":          {"score": 3, "points": 12, "comment": "..."},
    "pronunciation":    {"score": 4, "points": 12, "comment": "..."}
  },
  "strengths": ["..."],
  "weaknesses": ["..."],
  "recommendations": ["..."],
  "next_level_readiness": "..."
}"""


def _fmt_fillers(metrics: Dict[str, Any]) -> str:
    fillers = metrics.get("fillers") or {}
    if not fillers:
        return "none detected"
    return ", ".join(
        f"\u201e{k}\" x{v}"
        for k, v in sorted(fillers.items(), key=lambda kv: -kv[1])
    )


def build_assessment_markdown(task: dict, data: Dict[str, Any]) -> str:
    """Build the complete, self-contained CEFR assessment file.

    data keys: transcript (str), words (raw whisper word dicts, optional),
    metrics (dict, optional - computed when missing), duration_s (float, optional),
    recorded_at (str, optional).
    """
    words = data.get("words") or []
    transcript = (data.get("transcript") or "").strip()
    metrics = data.get("metrics") or compute_speech_metrics(words, data.get("duration_s"))
    ev = word_evidence(words) if words else None
    summ = ev["summary"] if ev else None
    recorded_at = data.get("recorded_at") or datetime.now().strftime("%Y-%m-%d %H:%M")

    L: List[str] = []
    ap = L.append

    ap(f"# CEFR Speaking Assessment — {task.get('title', task.get('slug', 'Exam task'))}")
    ap("")
    ap(f"> Generated by the LanguageShadow Exam Trainer on {recorded_at}. "
       "Paste this ENTIRE file into any LLM (ChatGPT, Claude, Gemini, or a local model). "
       "The file contains the task, the measured performance and the full official-style "
       "rubric — no API key, no account, no subscription needed.")
    ap("")

    # --- 1. Role -----------------------------------------------------------
    ap("## 1. Your Role")
    ap("")
    ap("You are a certified CEFR German speaking examiner for the "
       f"**{task.get('exam') or 'Goethe-Institut / ÖSD / telc'}** format. "
       "You assess the learner's performance below STRICTLY from the evidence in this file — "
       "you do NOT hear any audio. Be as strict, transparent and evidence-based as a real "
       "examiner: quote the transcript when you judge a criterion, never invent facts, and "
       "apply the rubric exactly as written. Recommended settings for reproducibility: "
       "temperature 0.1 (if your LLM supports it).")
    ap("")

    # --- 2. Task -----------------------------------------------------------
    ap("## 2. The Exam Task")
    ap("")
    ap("| Field | Value |")
    ap("|---|---|")
    ap(f"| Level | **{task.get('level')}** |")
    if task.get("exam"):
        ap(f"| Exam | {task['exam']} |")
    if task.get("task_type"):
        ap(f"| Task type | {task['task_type']} |")
    if task.get("exam_source"):
        ap(f"| Source | {task['exam_source']} |")
    ap(f"| Prep time | {task.get('prep_seconds', 60)} s |")
    ap(f"| Planned speaking time | {task.get('speak_seconds', 120)} s |")
    ap(f"| Actual measured duration | {metrics.get('duration_s', 0)} s |")
    ap(f"| Language | {task.get('language', 'de-DE')} |")
    ap("")
    if task.get("situation"):
        ap("### Situation (given to the learner)")
        ap("")
        for line in task["situation"].splitlines():
            ap(f"> {line}".rstrip())
        ap("")
    if task.get("requirements"):
        ap("### Required points (the learner should address ALL of them)")
        ap("")
        for r in task["requirements"]:
            ap(f"- [ ] {r}")
        ap("")
    if task.get("description"):
        ap("### Task instructions shown to the learner")
        ap("")
        ap(task["description"])
        ap("")

    # --- 3. Performance ----------------------------------------------------
    ap("## 3. The Learner's Performance (measured by the speech module)")
    ap("")
    ap("### 3.1 Transcript (verbatim ASR output, German)")
    ap("")
    if transcript:
        ap(f"```text")
        ap(transcript)
        ap("```")
    else:
        ap("**No speech was recognised in the recording.** If this is correct, "
           "score all criteria 1 and state that no speech was produced.")
    ap("")
    ap("### 3.2 Fluency metrics")
    ap("")
    ap("| Metric | Value |")
    ap("|---|---|")
    ap(f"| Words spoken | {metrics.get('word_count', 0)} |")
    ap(f"| Total duration | {metrics.get('duration_s', 0)} s |")
    ap(f"| Speech span (first→last word) | {metrics.get('speech_duration_s', 0)} s |")
    ap(f"| Pace, gross WPM | {metrics.get('wpm', 0)} |")
    ap(f"| Pace, net WPM (pause time removed) | {metrics.get('wpm_net', 0)} |")
    ap(f"| Pauses > {PAUSE_THRESHOLD_S}s | {metrics.get('pause_count', 0)} "
       f"(total {metrics.get('pause_total_s', 0)} s, longest {metrics.get('longest_pause_s', 0)} s) |")
    ap(f"| Filled pauses | {metrics.get('filler_count', 0)} — {_fmt_fillers(metrics)} |")
    ap("")
    pauses = metrics.get("pauses") or []
    if pauses:
        ap("<details><summary>Pause profile (where the longest hesitations happened)</summary>")
        ap("")
        ap("| # | After word | At (s) | Length (s) |")
        ap("|---|---|---|---|")
        for i, p in enumerate(pauses, start=1):
            ap(f"| {i} | {p.get('after_word','')} | {p.get('at_s',0)} | {p.get('duration_s',0)} |")
        ap("")
        ap("</details>")
        ap("")
    ap("*Note: „also\" is counted as a filler because it is often used as one, but it can "
       "also be a normal conjunction — treat it as weak evidence.*")
    ap("")

    # --- 4. Pronunciation evidence -----------------------------------------
    ap("## 4. Pronunciation Evidence (word-level)")
    ap("")
    ap("You do NOT hear audio. Pronunciation is scored ONLY from the evidence in this "
       "section: `accuracy` is the speech-recogniser's per-word confidence (1.0 = clearly "
       "recognised, 0.0 = unrecognisable). A word the recogniser could not match is very "
       "often a word the examiner would also have struggled to understand.")
    ap("")
    if ev and summ:
        flagged = [w for w in ev["words"] if w["error_type"] != "ok"]
        ap("### 4.1 Words the ASR was NOT confident about (accuracy < "
           f"{ACC_GOOD:.2f})")
        ap("")
        ap("| # | Word (as heard) | Accuracy | Error type | Note |")
        ap("|---|---|---|---|---|")
        for w in flagged:
            ap(f"| {w['index']} | {w['word']} | {w['accuracy']:.2f} | {w['error_type']} "
               f"| {w.get('note', '—')} |")
        if not flagged:
            ap("| — | (none — every word was recognised with accuracy ≥ 0.90) | — | — | — |")
        ap("")
        ap(f"*{summ['ok_words']} of {summ['total_words']} words reached accuracy ≥ 0.90 "
           "and are omitted from the table; the bucket summary below is authoritative.*")
        ap("")
        ap("### 4.2 Summary")
        ap("")
        ap(f"- Words with accuracy ≥ {ACC_GOOD:.2f}: **{summ['ok_words']}** ({summ['ok_pct']}%)")
        ap(f"- Words with accuracy {ACC_LOW:.2f}–0.89: **{summ['unclear_words']}** ({summ['unclear_pct']}%)")
        ap(f"- Words with accuracy < {ACC_LOW:.2f}: **{summ['mispronounced_words']}** ({summ['mispronounced_pct']}%)")
        if summ.get("umlaut_flagged"):
            ap(f"- Flagged words containing umlauts/ß: **{summ['umlaut_flagged']}** "
               "(umlaut mistakes change meaning in German — weigh them)")
        if summ.get("lowest_words"):
            ap(f"- Weakest words: {', '.join(summ['lowest_words'][:10])}")
        ap("")
    else:
        ap("*Word-level evidence is not available for this session — only the transcript "
           "above. In that case score pronunciation conservatively (never above 3) and say "
           "why.*")
        ap("")
    ap("> **Limitation:** Pronunciation is scored from ASR word-level confidence, not "
       "from the raw audio. Intonation, sentence melody and regional accent are NOT "
       "assessed. This is an approximation — keep the pronunciation score consistent with "
       "the evidence and do not over-interpret it.")
    ap("")

    # --- 5. Rubric -----------------------------------------------------------
    ap("## 5. Assessment Instructions (the rubric)")
    ap("")
    ap("Score each criterion 1–5 (integers), then apply the weights. German exams are "
       "criterion-based: read the descriptor rows and pick the one that fits best.")
    ap("")
    ap("### Step 1 — Score the four text criteria from the transcript")
    ap("")
    ap("| Score | General descriptor (all criteria) |")
    ap("|---|---|")
    ap("| 5 | Fully appropriate for the level; task fully achieved; ideas connected; wide, precise vocabulary; varied, mostly correct structures |")
    ap("| 4 | Good command; task mostly achieved; minor slips that never block understanding |")
    ap("| 3 | Acceptable for the level; task partly achieved; noticeable errors and limitations, but the message is clear |")
    ap("| 2 | Limited; task only touched; frequent errors and very simple language make understanding effortful |")
    ap("| 1 | Very limited; requirements essentially not addressed; fragmentary, hard to understand |")
    ap("")
    for _key, label, _w, desc in _CRITERIA[:-1]:
        ap(f"- **{label}** — {desc}")
    ap("")
    ap("### Step 2b — Pronunciation scoring (from Section 4 evidence only)")
    ap("")
    ap("| Score | Condition |")
    ap("|---|---|")
    ap(f"| 5 | ≥ 90% of words at accuracy ≥ {ACC_GOOD:.2f}; no systematic errors |")
    ap(f"| 4 | ≥ 80% of words at accuracy ≥ {ACC_GOOD:.2f}; minor issues only |")
    ap(f"| 3 | ≥ 65% of words at accuracy ≥ {ACC_GOOD:.2f}; noticeable but understandable errors |")
    ap(f"| 2 | ≥ 50% of words at accuracy ≥ {ACC_GOOD:.2f}; frequent errors, effort needed to understand |")
    ap(f"| 1 | < 50% of words at accuracy ≥ {ACC_GOOD:.2f}; very hard to understand |")
    ap("")
    ap("Also weigh, when the evidence shows it:")
    ap("")
    ap("- **Umlaut errors (Ö, Ä, Ü, ß)** — they change meaning in German → penalty")
    ap("- **Number confusion (13/30, 15/50: dreizehn/dreißig, fünfzehn/fünfzig)** — serious, affects task completion")
    ap("- **Swallowed word endings (-e, -er, -en)** — grammar/pronunciation boundary")
    ap("- **Consistent vs random errors** — consistent errors are more serious")
    ap("")
    ap("### Step 3 — Apply the weights")
    ap("")
    ap("| Criterion | Weight |")
    ap("|---|---|")
    for _k, label, w, _d in _CRITERIA:
        ap(f"| {label} | {w}% |")
    ap("")
    ap("```text")
    ap("Score = (TaskFulfillment/5 × 30) + (Coherence/5 × 15) + (Vocabulary/5 × 20)")
    ap("      + (Grammar/5 × 20) + (Pronunciation/5 × 15)")
    ap("```")
    ap("")
    ap("### Step 4 — Map the score to a CEFR level and result")
    ap("")
    ap("| Score | CEFR estimate | Result |")
    ap("|---|---|---|")
    ap("| 90–100 | B2 | Pass with distinction |")
    ap("| 75–89 | B1 | Pass |")
    ap("| 60–74 | A2+ / B1− | Borderline |")
    ap("| 40–59 | A2 | Fail (for a B1/B2 exam) |")
    ap("| 0–39 | A1 or below | Fail |")
    ap("")
    ap(f"### Level expectations for this exam ({task.get('level')})")
    ap("")
    ap(_LEVEL_EXPECTATIONS.get(task.get("level", ""), _LEVEL_EXPECTATIONS["B1"]))
    ap("")

    # --- 6. Output format ----------------------------------------------------
    ap("## 6. Required Output Format")
    ap("")
    ap("Respond with **exactly** this structure:")
    ap("")
    ap("1. First, the JSON object (and nothing else before it):")
    ap("")
    ap("```json")
    ap(_JSON_OUTPUT_EXAMPLE)
    ap("```")
    ap("")
    ap("Rules for the JSON:")
    ap("")
    ap("- `score` values are integers 1–5; `points` = score × weight / 5.")
    ap("- `overall_score` = the weighted sum (0–100, integer).")
    ap(f"- `overall_level` ∈ A2 | B1 | B2 (the level the performance actually demonstrates, "
       f"which can be lower than the exam level {task.get('level')}).")
    ap("- `exam_result` ∈ PASS | BORDERLINE | FAIL, judged against the table above for "
       f"the **{task.get('exam') or task.get('level')}** exam.")
    ap("- `comment`s must quote or cite concrete transcript/evidence lines.")
    ap("- `next_level_readiness`: 2–3 sentences on how far the learner is from the next level.")
    ap("")
    ap("2. Then, a learner-friendly summary **in German** (max ~300 words): what went "
       "well, what held the score back, and the three most important things to practise "
       "next. Address the learner with \"du\".")
    ap("")

    # --- 7. Provenance --------------------------------------------------------
    ap("## 7. Data Provenance (for your calibration)")
    ap("")
    ap("- Transcript and word confidences: local faster-whisper ASR "
       f"(language {task.get('language', 'de-DE')}); ASR mistakes can LOOK like "
       "pronunciation mistakes — when in doubt, stay conservative.")
    ap("- Fluency metrics are computed from word timestamps; VAD may trim leading silence.")
    ap("- This file is fully self-contained: judge ONLY from Sections 2–4, do not ask "
       "for more information, do not request the audio.")
    ap("")

    return "\n".join(L)


def export_filename(task: dict, data: Dict[str, Any]) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    slug = re.sub(r"[^A-Za-z0-9_\-]+", "", task.get("slug", "task")) or "task"
    return f"assessment_{task.get('level', 'B1')}_{slug}_{stamp}.md"


# ---------------------------------------------------------------------------
# CLI smoke test:  python3 exam.py   (parses the task library, prints a summary)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    ts = list_tasks()
    print(f"{len(ts)} tasks found:")
    for t in ts:
        print(f"  [{t['level']}] {t['slug']:<28} {t['title'][:48]:<48}"
              f" req={len(t['requirements'])} speak={t['speak_seconds']}s")
    demo_words = [
        {"word": "ich", "start": 0.0, "end": 0.2, "probability": 0.98},
        {"word": "äh", "start": 0.3, "end": 0.5, "probability": 0.99},
        {"word": "wohne", "start": 1.4, "end": 1.9, "probability": 0.42},
        {"word": "in", "start": 1.95, "end": 2.0, "probability": 0.95},
        {"word": "Köln.", "start": 2.1, "end": 2.6, "probability": 0.88},
    ]
    print("\nmetrics demo:", json.dumps(compute_speech_metrics(demo_words, 4.2), ensure_ascii=False))
    print("\nevidence demo:", json.dumps(word_evidence(demo_words)["summary"], ensure_ascii=False))
