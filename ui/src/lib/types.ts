/* LanguageShadow shared types — match the worker's API.md contract */

export type WordErrorType = 'None' | 'Mispronunciation' | 'Omitted' | 'Inserted';

export interface WordVerdict {
  word: string;
  accuracy: number;            // 0..100
  errorType: WordErrorType;
}

export interface Subscores {
  accuracy: number;
  fluency: number;
  prosody: number;
  completeness: number;
}

export interface Pace {
  wpm: number;
  duration_ms: number;
}

export interface AssessResponse {
  status: 'OK' | 'ERROR';
  overall?: number;
  subscores?: Subscores;
  pace?: Pace;
  words?: WordVerdict[];
  recognized?: string;
  cefr?: string;
  pause_count?: number;
  n_matched?: number;
  n_mispronounced?: number;
  n_omitted?: number;
  n_inserted?: number;
  error?: string;
}

export interface HealthResponse {
  manager: { pid: number; rss_mb: number };
  worker: {
    state: 'RUNNING' | 'STOPPED' | 'STARTING' | 'STOPPING' | 'FROZEN';
    pid?: number;
    rss_mb?: number;
    model_loaded?: boolean;
    idle_seconds?: number;
    idle_timeout?: number;
    start_count?: number;
  };
  ram_budget_mb: number;
  model: string;
}

export type WorkerState = HealthResponse['worker']['state'];

/* Live Caption: streaming partial transcript frame (free-speech mode) */
export interface PartialFrame {
  type: 'partial';
  session_id: string;
  text: string;
  words: number;
  elapsed_s: number;
}

/* Logs page: one parsed line from the shared manager+worker log file */
export interface SystemLogLine {
  ts: number;
  ts_str: string;
  source: 'manager' | 'worker' | 'system';
  level: string;
  msg: string;
}

export interface SystemLogResponse {
  file: string;
  size_bytes: number;
  lines: SystemLogLine[];
}

/* ------------------------------------------------------------------ */
/* Exam page (CEFR speaking practice for Goethe / ÖSD / telc)          */
/* ------------------------------------------------------------------ */

/** One task from tasks/<LEVEL>/*.md (parsed by the manager). */
export interface ExamTask {
  level: 'A2' | 'B1' | 'B2' | string;
  slug: string;
  file: string;
  title: string;
  exam: string;
  exam_source: string;
  task_type: string;
  situation: string;
  requirements: string[];
  prep_seconds: number;
  speak_seconds: number;
  language: string;
  description: string;
}

/** Final frame of a mode:"exam" WS session (worker.py finalize()). */
export interface ExamFinalFrame {
  type: 'final';
  session_id: string;
  status: 'OK' | 'ERROR';
  error?: string;
  recognized?: string;
  /** raw word dicts: {word, start, end, probability} */
  words?: Array<{ word: string; start: number; end: number; probability: number }>;
  evidence?: {
    words: Array<{
      index: number; word: string; start: number; end: number;
      accuracy: number; error_type: 'ok' | 'unclear' | 'mispronounced';
      note?: string;
    }>;
    summary: {
      total_words: number; ok_words: number; ok_pct: number;
      unclear_words: number; unclear_pct: number;
      mispronounced_words: number; mispronounced_pct: number;
      umlaut_flagged: number; lowest_words: string[];
    };
  };
  metrics?: ExamMetrics;
  duration_s?: number;
}

export interface ExamMetrics {
  word_count: number;
  duration_s: number;
  speech_duration_s: number;
  wpm: number;
  wpm_net: number;
  pause_count: number;
  pause_total_s: number;
  longest_pause_s: number;
  filler_count: number;
  fillers: Record<string, number>;
}

/** POST /api/exam/export — the self-contained assessment markdown. */
export interface ExamExportResponse {
  filename: string;
  markdown: string;
  task: { level: string; slug: string; title: string; exam: string };
  error?: string;
}
