/* Small status store shared across pages — keeps the topbar in sync */

import { writable } from 'svelte/store';
import type { HealthResponse, WorkerState } from '$lib/types';
import { apiClient } from '$lib/api';

export const health = writable<HealthResponse | null>(null);

let timer: ReturnType<typeof setInterval> | null = null;
let started = false;

export function startHealthPolling() {
  if (started) return;
  started = true;
  timer = setInterval(poll, 2000);
  poll();
}

async function poll() {
  try {
    const h = await apiClient.health();
    health.set(h);
  } catch {
    /* ignore - manager may be briefly unreachable */
  }
}

export function stopHealthPolling() {
  if (timer) clearInterval(timer);
  timer = null;
  started = false;
}

/* Helpers for the topbar pill */
export function totalRamMb(h: HealthResponse | null): number {
  if (!h) return 0;
  return (h.manager?.rss_mb || 0) + (h.worker?.rss_mb || 0);
}

export function workerState(h: HealthResponse | null): WorkerState | 'UNKNOWN' {
  return h?.worker?.state ?? 'UNKNOWN';
}
