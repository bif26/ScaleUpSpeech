/* LanguageShadow REST API client */

import type { AssessResponse, ExamExportResponse, ExamTask, HealthResponse, SystemLogResponse } from './types';

const API_BASE = `http://${location.hostname}:8765`;
const WS_URL = `ws://${location.hostname}:8765/ws/transcribe`;

async function api<T = any>(path: string, method = 'GET', body?: any): Promise<T> {
  const opt: RequestInit = { method, headers: {} };
  if (body) {
    (opt.headers as any)['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body);
  }
  const r = await fetch(API_BASE + path, opt);
  if (!r.ok) throw new Error(`${path}: ${r.status} ${await r.text()}`);
  return await r.json();
}

export const apiClient = {
  base: API_BASE,

  health(): Promise<HealthResponse> {
    return api('/api/health');
  },

  startWorker(): Promise<any> {
    return api('/manager/start', 'POST');
  },

  stopWorker(): Promise<any> {
    return api('/manager/stop', 'POST');
  },

  freezeWorker(): Promise<any> {
    return api('/manager/freeze', 'POST');
  },

  thawWorker(): Promise<any> {
    return api('/manager/thaw', 'POST');
  },

  workerStatus(): Promise<any> {
    return api('/manager/status');
  },

  logs(limit = 10): Promise<{ logs: any[] }> {
    return api(`/api/logs?limit=${limit}`);
  },

  /* Manager-side log endpoints — work even when the worker is stopped. */
  systemLogs(tail = 300, source = 'all'): Promise<SystemLogResponse> {
    return api(`/api/logs/system?tail=${tail}&source=${encodeURIComponent(source)}`);
  },

  workerOutput(tailLines = 200): Promise<{ file: string; lines: string[] }> {
    return api(`/api/logs/output?tail_lines=${tailLines}`);
  },

  stats(): Promise<any> {
    return api('/api/stats');
  },

  languages(): Promise<Record<string, any>> {
    return api('/api/languages');
  },

  captions(since = 0): Promise<{ captions: any[] }> {
    return api(`/api/captions?since=${since}`);
  },

  clearCaptions(): Promise<any> {
    return api('/api/captions', 'DELETE');
  },

  assess(payload: {
    audio_base64: string;
    reference_text: string;
    language: string;
  }): Promise<AssessResponse> {
    return api('/api/assess-speech', 'POST', payload);
  },

  /* Exam page (CEFR speaking practice) — manager-side, always available. */
  examTasks(): Promise<{ levels: string[]; tasks: ExamTask[] }> {
    return api('/api/exam/tasks');
  },

  examExport(payload: {
    level: string;
    slug: string;
    transcript: string;
    words: Array<{ word: string; start: number; end: number; probability: number }>;
    metrics?: any;
    duration_s?: number;
  }): Promise<ExamExportResponse> {
    return api('/api/exam/export', 'POST', payload);
  },

  wsUrl: WS_URL,
};
