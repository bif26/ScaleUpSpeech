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
