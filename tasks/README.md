# Exam Task Library

Tasks for the **Exam** page (`/exam`) — CEFR speaking practice for
**Goethe / ÖSD / telc** exams at levels **A2, B1, B2** (German, `de-DE`).

## Adding a task = dropping a file in a folder. Nothing else.

1. Create a file inside the level folder:

```
tasks/
├── A2/  01_mein_tagesablauf.md ...
├── B1/  01_sprachkurs_anrufen.md ...
└── B2/  01_soziale_medien.md ...
```

2. Use exactly this form (copy any existing file and edit it):

```markdown
---
level: B1
exam: Goethe-Zertifikat B1
exam_source: "Goethe B1 Sprechen Teil 2, ..."
task_type: Telefonische Anfrage
situation: |
  [the full situation text from the exam, in German]
requirements:
  - [required point 1]
  - [required point 2]
  - [required point 3]
  - [required point 4]
  - [required point 5]
prep_seconds: 60
speak_seconds: 120
language: de-DE
---

# [Task title in German]

[optional short instructions for the learner]
```

## Field reference

| Field | Meaning |
|---|---|
| `level` | `A2`, `B1` or `B2` (the folder name wins if they differ) |
| `exam` | exam brand shown to the learner, e.g. `Goethe-Zertifikat B1` |
| `exam_source` | where the situation comes from; for format practice write `Formatübung nach ...` |
| `task_type` | short type label, e.g. `Über ein Thema sprechen` |
| `situation` | the situation text the learner must speak about (shown during prep) |
| `requirements` | the points the answer must address — the LLM scores Task Fulfillment against exactly this list |
| `prep_seconds` | preparation countdown before recording starts |
| `speak_seconds` | planned speaking time; the recorder auto-stops after this |
| `language` | keep `de-DE` (Whisper receives `de`) |

Rules of thumb:
- File name = slug shown in the exported assessment file (`01_sprachkurs_anrufen`).
- 4–6 concrete requirements work best: the LLM checks each one against the transcript.
- `.json` files also work: the same fields as a JSON object, plus `description`.
- The Exam page picks up new files immediately — no restart needed.

## Honesty note

The seed tasks are **format practice tasks** modelled on the structure of the
real exams (`exam_source` says `Formatübung nach ...`), not verbatim copies of
released official papers. When you add tasks from real released exams, cite
the exact release in `exam_source`.
