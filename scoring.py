"""
LanguageShadow - Phonetic pronunciation scoring engine

Implements the scoring algorithm from idea.md:
  - faster-whisper transcribes the audio with word-level timestamps + probabilities
  - the recognized words are aligned against the reference text
  - phonetic similarity is measured with the `phonetics` library (Metaphone),
    so "colour" / "color" / "kaler" all match if they sound the same
  - pause penalty (gaps > 1.1 s between words) lowers fluency
  - WPM is computed from word count / audio duration
  - CEFR level (A2 / B1 / B2 / B2+) is derived from overall score + WPM

Returns data in the exact shape the existing browser extension expects
(see API.md):
  status: "OK" | "ERROR"
  overall: int 0..100
  subscores: { accuracy, fluency, prosody, completeness }  (each 0..100)
  pace: { wpm, duration_ms }
  words: [ { word, accuracy, errorType: "None"|"Mispronunciation"|"Omitted"|"Inserted" } ]
  recognized: str
"""

from __future__ import annotations

import re
import statistics
from typing import List, Optional, Tuple

try:
    import phonetics
    _HAVE_PHONETICS = True
except Exception:
    _HAVE_PHONETICS = False


# ---------------------------------------------------------------------------
# Tokenisation
# ---------------------------------------------------------------------------
_WORD_RE = re.compile(r"[A-Za-z0-9']+(?:[-][A-Za-z0-9']+)*")


def _clean_token(w: str) -> str:
    return w.lower().strip(".,?!\"'’`()[]:; ")


def tokenize(text: str) -> List[str]:
    return [_clean_token(w) for w in _WORD_RE.findall(text or "") if _clean_token(w)]


# ---------------------------------------------------------------------------
# Phonetic similarity
# ---------------------------------------------------------------------------
def metaphone(w: str) -> str:
    if not _HAVE_PHONETICS or not w:
        return w.upper()
    try:
        return phonetics.metaphone(w)
    except Exception:
        return w.upper()


def phonetic_match(a: str, b: str) -> bool:
    """True if a and b sound the same under Metaphone."""
    if not a or not b:
        return False
    return metaphone(a) == metaphone(b)


def phonetic_similarity(a: str, b: str) -> float:
    """0..1 — 1.0 if Metaphone codes are equal, else falls back to length-ratio."""
    if not a or not b:
        return 0.0
    ma, mb = metaphone(a), metaphone(b)
    if ma == mb:
        return 1.0
    # Partial credit if the codes share a long common prefix (e.g., "AKADMK"
    # vs "AKADM" - missing trailing consonant, close enough).
    common = 0
    for x, y in zip(ma, mb):
        if x == y: common += 1
        else: break
    if common == 0:
        return 0.0
    return min(0.6, common / max(len(ma), len(mb)))


# ---------------------------------------------------------------------------
# Alignment (word-level, with phonetic substitution)
# ---------------------------------------------------------------------------
def _align(ref: List[str], hyp: List[str]) -> List[Tuple[str, str, str]]:
    """Return [(ref_word, hyp_word, op)] where op in {match, sub, del, ins}.

    Cost model:
      match:   0   (or 1 - phonetic_similarity for soft match)
      sub:     0.4 (counts as mispronunciation)
      del:     1.0 (ref word omitted)
      ins:     0.7 (extra hyp word inserted)
    """
    n, m = len(ref), len(hyp)
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1): dp[i][0] = i * 1.0
    for j in range(1, m + 1): dp[0][j] = j * 0.7
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            sim = phonetic_similarity(ref[i - 1], hyp[j - 1])
            cost_sub = 0.4 * (1.0 - sim) + (0.0 if sim >= 0.6 else 0.4)
            dp[i][j] = min(
                dp[i - 1][j] + 1.0,        # deletion
                dp[i][j - 1] + 0.7,        # insertion
                dp[i - 1][j - 1] + cost_sub,  # match/sub
            )
    # Backtrace
    i, j = n, m
    out = []
    while i > 0 or j > 0:
        cur = dp[i][j]
        up = dp[i - 1][j] + 1.0 if i > 0 else float("inf")
        left = dp[i][j - 1] + 0.7 if j > 0 else float("inf")
        diag_cost = 0.0
        if i > 0 and j > 0:
            sim = phonetic_similarity(ref[i - 1], hyp[j - 1])
            diag_cost = 0.4 * (1.0 - sim) + (0.0 if sim >= 0.6 else 0.4)
        diag = dp[i - 1][j - 1] + diag_cost if i > 0 and j > 0 else float("inf")
        if diag <= up and diag <= left and i > 0 and j > 0:
            sim = phonetic_similarity(ref[i - 1], hyp[j - 1])
            op = "match" if sim >= 0.6 else "sub"
            out.append((ref[i - 1], hyp[j - 1], op))
            i -= 1; j -= 1
        elif up <= left and i > 0:
            out.append((ref[i - 1], "", "del"))
            i -= 1
        else:
            out.append(("", hyp[j - 1], "ins"))
            j -= 1
    out.reverse()
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def score_assessment(
    reference_text: str,
    whisper_segments: List[dict],
    *,
    pause_threshold_s: float = 1.1,
    pause_penalty_per_pause: float = 8.0,
    ideal_wpm_range: Tuple[int, int] = (110, 150),
) -> dict:
    """Score one assessment attempt.

    Args:
        reference_text: what the user was supposed to say.
        whisper_segments: list of segment dicts from faster-whisper with
            {"text", "start", "end", "words": [{"word","start","end","probability"}]}.
        pause_threshold_s: gaps between consecutive words longer than this
            count as hesitation pauses.
        pause_penalty_per_pause: points subtracted from fluency for each pause.
        ideal_wpm_range: (min, max) — fluency is 100 inside this range,
            scaled down outside it.

    Returns:
        Dict in the API.md contract shape.
    """
    # 1) Flatten whisper output into a list of (word, start, end, prob).
    hyp_words: List[Tuple[str, float, float, float]] = []
    for seg in whisper_segments or []:
        for w in seg.get("words") or []:
            tok = _clean_token(w.get("word", ""))
            if not tok:
                continue
            hyp_words.append((
                tok,
                float(w.get("start", 0.0)),
                float(w.get("end", 0.0)),
                float(w.get("probability", 0.0)),
            ))
    # Fallback: segments without word timings.
    if not hyp_words:
        for seg in whisper_segments or []:
            for tok in tokenize(seg.get("text", "")):
                hyp_words.append((tok, float(seg.get("start", 0.0)),
                                  float(seg.get("end", 0.0)), 0.0))

    ref_words = tokenize(reference_text)
    if not ref_words:
        return {
            "status": "ERROR",
            "error": "reference_text is empty after tokenisation",
        }
    if not hyp_words:
        return {
            "status": "ERROR",
            "error": "No speech detected. Please speak louder or closer to the mic.",
        }

    # 2) Pause penalty — count gaps > pause_threshold_s.
    pause_count = 0
    for i in range(1, len(hyp_words)):
        gap = hyp_words[i][1] - hyp_words[i - 1][2]
        if gap > pause_threshold_s:
            pause_count += 1
    pause_penalty = pause_count * pause_penalty_per_pause

    # 3) Word-level alignment + per-word scoring.
    aligned = _align(ref_words, [w[0] for w in hyp_words])

    words_out = []
    matched_accuracies: List[float] = []
    n_matched = 0
    n_omitted = 0
    n_inserted = 0
    n_mispron = 0
    hyp_idx = 0

    for ref_w, hyp_w, op in aligned:
        if op == "match":
            # Word accuracy = 60% phonetic similarity + 40% whisper probability
            sim = phonetic_similarity(ref_w, hyp_w)
            prob = hyp_words[hyp_idx][3] if hyp_idx < len(hyp_words) else 0.0
            acc = max(0, min(100, int((sim * 0.6 + prob * 0.4) * 100)))
            words_out.append({
                "word": ref_w,
                "accuracy": acc,
                "errorType": "None",
            })
            matched_accuracies.append(acc)
            n_matched += 1
            hyp_idx += 1
        elif op == "sub":
            sim = phonetic_similarity(ref_w, hyp_w)
            prob = hyp_words[hyp_idx][3] if hyp_idx < len(hyp_words) else 0.0
            acc = max(0, min(60, int((sim * 0.5 + prob * 0.3) * 100)))
            words_out.append({
                "word": ref_w,
                "accuracy": acc,
                "errorType": "Mispronunciation",
            })
            matched_accuracies.append(acc)
            n_mispron += 1
            hyp_idx += 1
        elif op == "del":
            words_out.append({
                "word": ref_w,
                "accuracy": 0,
                "errorType": "Omitted",
            })
            n_omitted += 1
        else:  # ins
            # Inserted word — surface it so the panel can show it, but
            # the API.md contract says words[] is for reference words only.
            # We add it at the end with errorType=Inserted; the panel
            # will colour it red.
            words_out.append({
                "word": hyp_w,
                "accuracy": 0,
                "errorType": "Inserted",
            })
            n_inserted += 1
            hyp_idx += 1

    # 4) Audio duration + WPM.
    if hyp_words:
        audio_start = min(w[1] for w in hyp_words)
        audio_end = max(w[2] for w in hyp_words)
        duration_s = max(audio_end - audio_start, 0.001)
    else:
        duration_s = 0.001
    words_spoken = max(1, len([w for w in hyp_words]))
    wpm = int((words_spoken / duration_s) * 60.0)

    # 5) Sub-scores (each 0..100).
    # accuracy = mean of matched-word accuracies.
    accuracy = int(statistics.mean(matched_accuracies)) if matched_accuracies else 0

    # completeness = % of reference words that were spoken (matched or subbed).
    completeness = int(100 * (n_matched + n_mispron) / len(ref_words))

    # fluency = base by WPM, minus pause penalty.
    lo, hi = ideal_wpm_range
    if wpm >= hi:
        fluency_base = 95
    elif wpm >= lo:
        fluency_base = 80
    elif wpm >= 60:
        fluency_base = 65
    elif wpm >= 40:
        fluency_base = 50
    else:
        fluency_base = 35
    fluency = max(10, int(fluency_base - pause_penalty))

    # prosody — we don't measure pitch here, so approximate from the
    # uniformity of inter-word gaps + average whisper confidence.
    gaps = []
    for i in range(1, len(hyp_words)):
        g = hyp_words[i][1] - hyp_words[i - 1][2]
        if 0 <= g <= pause_threshold_s:
            gaps.append(g)
    if gaps:
        # Low variance = steady rhythm = high prosody.
        try:
            mean = statistics.mean(gaps)
            stdev = statistics.pstdev(gaps) if len(gaps) > 1 else 0.0
            rhythm = max(0.0, 1.0 - stdev / max(mean, 0.001))
        except Exception:
            rhythm = 0.5
    else:
        rhythm = 0.5
    avg_conf = (sum(w[3] for w in hyp_words) / len(hyp_words)) if hyp_words else 0.0
    prosody = int(100 * (0.7 * rhythm + 0.3 * avg_conf))
    prosody = max(0, min(100, prosody))

    # 6) Overall = weighted mix per API.md suggestion.
    overall = int(
        0.45 * accuracy +
        0.20 * fluency +
        0.15 * prosody +
        0.20 * completeness
    )
    overall = max(0, min(100, overall))

    # 7) CEFR (extra field, not in API.md but useful for the user).
    if overall >= 85 and wpm >= 110:
        cefr = "B2+"
    elif overall >= 70 and wpm >= 85:
        cefr = "B2"
    elif overall >= 55 and wpm >= 60:
        cefr = "B1"
    elif overall >= 40:
        cefr = "A2"
    else:
        cefr = "Below A2"

    recognized = " ".join(w[0] for w in hyp_words)

    return {
        "status": "OK",
        "overall": overall,
        "subscores": {
            "accuracy": accuracy,
            "fluency": fluency,
            "prosody": prosody,
            "completeness": completeness,
        },
        "pace": {
            "wpm": wpm,
            "duration_ms": int(duration_s * 1000),
        },
        "words": words_out,
        "recognized": recognized,
        # Extra debugging fields (panel ignores them).
        "cefr": cefr,
        "pause_count": pause_count,
        "n_matched": n_matched,
        "n_mispronounced": n_mispron,
        "n_omitted": n_omitted,
        "n_inserted": n_inserted,
    }


# ---------------------------------------------------------------------------
# Live scoring (used by the web UI WS endpoint)
# ---------------------------------------------------------------------------
def incremental_score(reference_text: str, partial_segments: List[dict]) -> dict:
    """Cheap live score used while the user is still speaking."""
    ref_words = tokenize(reference_text)
    hyp_words = []
    for seg in partial_segments or []:
        for w in seg.get("words") or []:
            tok = _clean_token(w.get("word", ""))
            if tok:
                hyp_words.append(tok)

    aligned = _align(ref_words, hyp_words)
    n_correct = sum(1 for r, h, op in aligned if op == "match")
    n_ref = max(len(ref_words), 1)
    accuracy = int(100 * n_correct / n_ref)
    progress = min(1.0, len(hyp_words) / n_ref) if ref_words else 0.0

    # Reuse the same per-word rendering as the final pass.
    words_out = []
    hyp_idx = 0
    for ref_w, hyp_w, op in aligned:
        if op == "match":
            sim = phonetic_similarity(ref_w, hyp_w)
            words_out.append({
                "word": ref_w, "accuracy": int(sim * 100),
                "errorType": "None",
            })
            hyp_idx += 1
        elif op == "sub":
            words_out.append({
                "word": ref_w, "accuracy": 30,
                "errorType": "Mispronunciation",
            })
            hyp_idx += 1
        elif op == "del":
            words_out.append({
                "word": ref_w, "accuracy": 0,
                "errorType": "Omitted",
            })
        else:
            words_out.append({
                "word": hyp_w, "accuracy": 0,
                "errorType": "Inserted",
            })
            hyp_idx += 1

    return {
        "progress": round(progress, 3),
        "words_spoken": len(hyp_words),
        "words_total": len(ref_words),
        "n_correct": n_correct,
        "accuracy": accuracy,
        "verdicts": words_out,
    }


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    sample_segments = [
        {
            "text": "the quick brown fox",
            "start": 0.0, "end": 1.5,
            "words": [
                {"word": "The", "start": 0.0, "end": 0.3, "probability": 0.9},
                {"word": "quick", "start": 0.35, "end": 0.7, "probability": 0.85},
                {"word": "brown", "start": 0.75, "end": 1.1, "probability": 0.8},
                {"word": "fox", "start": 1.15, "end": 1.5, "probability": 0.95},
            ],
        }
    ]
    result = score_assessment(
        "The quick brown fox jumps over the lazy dog",
        sample_segments,
    )
    import json
    print(json.dumps(result, indent=2))
