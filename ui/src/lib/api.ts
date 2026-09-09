/* LanguageShadow REST API client */

import type { AssessResponse, HealthResponse } from './types';

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

  wsUrl: WS_URL,
};
