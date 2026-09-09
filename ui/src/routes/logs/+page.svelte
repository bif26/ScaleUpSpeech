<script lang="ts">
  import { Card, CardHeader, CardTitle, CardContent, CardDescription, CardFooter } from '$lib/components/ui/card';
  import { Button } from '$lib/components/ui/button';
  import { Badge } from '$lib/components/ui/badge';
  import { Separator } from '$lib/components/ui/separator';
  import { toastStore } from '$lib/components/ui/toast';
  import { apiClient } from '$lib/api';
  import { health } from '$lib/stores/health';
  import { onMount } from 'svelte';
  import RefreshIcon from 'lucide-svelte/icons/refresh-cw';
  import PauseIcon from 'lucide-svelte/icons/pause';
  import PlayIcon from 'lucide-svelte/icons/play';
  import DownloadIcon from 'lucide-svelte/icons/download';
  import type { SystemLogResponse } from '$lib/types';

  // System log lines (parsed from the shared manager+worker log file).
  let systemLogs = $state<SystemLogResponse | null>(null);
  // Raw worker stdout (logs/worker.out — captures import errors / crashes
  // that happen before the logger is configured).
  let workerOutput = $state<{ file: string; lines: string[] } | null>(null);
  // Recent practice assessments (worker memory; empty when worker is stopped).
  let practiceLogs = $state<any[]>([]);
  let stats = $state<any>(null);

  let autoRefresh = $state(true);
  let sourceFilter = $state<'all' | 'manager' | 'worker' | 'system'>('all');
  let tail = $state(300);
  let loading = $state(false);
  let timer: ReturnType<typeof setInterval> | null = null;

  async function refresh() {
    loading = true;
    // System logs + worker output come from the MANAGER — always available,
    // even when the worker is stopped. These are the most useful for
    // debugging "logs page not found" / worker crashes.
    const tasks: Promise<void>[] = [
      (async () => {
        try {
          systemLogs = await apiClient.systemLogs(tail, sourceFilter);
        } catch (e: any) {
          toastStore.error('Failed to load system logs: ' + e.message);
        }
      })(),
      (async () => {
        try {
          workerOutput = await apiClient.workerOutput(200);
        } catch { /* ignore — endpoint always returns 200 with empty lines */ }
      })(),
    ];
    // Practice logs + stats come from the WORKER — only when running.
    if ($health?.worker?.state === 'RUNNING') {
      tasks.push(
        (async () => {
          try { practiceLogs = (await apiClient.logs(50)).logs; }
          catch { practiceLogs = []; }
        })(),
        (async () => {
          try { stats = await apiClient.stats(); }
          catch { stats = null; }
        })(),
      );
    } else {
      practiceLogs = [];
      stats = null;
    }
    await Promise.all(tasks);
    loading = false;
  }

  function startAuto() {
    if (timer) return;
    timer = setInterval(refresh, 3000);
  }
  function stopAuto() {
    if (timer) { clearInterval(timer); timer = null; }
  }
  function toggleAuto() {
    autoRefresh = !autoRefresh;
    if (autoRefresh) startAuto(); else stopAuto();
  }

  function levelClass(level: string): string {
    return level === 'error' ? 'border-destructive/40 text-destructive bg-destructive/10' :
           level === 'warning' ? 'border-warning/40 text-warning bg-warning/10' :
           level === 'info'    ? 'border-blue/40 text-blue bg-blue/10' :
           'border-overlay0/40 text-subtext1 bg-surface0/40';
  }
  function sourceClass(source: string): string {
    return source === 'manager' ? 'border-lavender/40 text-lavender bg-lavender/10' :
           source === 'worker'  ? 'border-sapphire/40 text-sapphire bg-sapphire/10' :
           'border-overlay0/40 text-subtext1 bg-surface0/40';
  }
  function formatSize(bytes: number): string {
    if (!bytes) return '0 B';
    const k = 1024;
    const units = ['B', 'KB', 'MB', 'GB'];
    const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(k)));
    return `${(bytes / Math.pow(k, i)).toFixed(1)} ${units[i]}`;
  }
  function timeOf(ts: number): string {
    if (!ts) return '—';
    return new Date(ts * 1000).toLocaleTimeString();
  }
  function downloadLogs() {
    // Open the manager endpoint in a new tab so the browser downloads the
    // raw JSON. Simple and works without a dedicated download endpoint.
    const url = `${apiClient.base}/api/logs/system?tail=2000&source=all`;
    window.open(url, '_blank');
  }

  onMount(() => {
    refresh();
    if (autoRefresh) startAuto();
    return () => stopAuto();
  });
</script>

<div class="space-y-6 max-w-5xl">
  <div class="flex items-center justify-between">
    <div>
      <h1 class="text-2xl font-semibold tracking-tight">Logs</h1>
      <p class="mt-1 text-sm text-muted-foreground">
        Manager + worker runtime logs, recent assessments, and worker stdout (crash output).
      </p>
    </div>
    <div class="flex items-center gap-2">
      <Button variant="outline" size="sm" onclick={toggleAuto}>
        {#if autoRefresh}
          <PauseIcon class="h-4 w-4" /> Pause
        {:else}
          <PlayIcon class="h-4 w-4" /> Resume
        {/if}
      </Button>
      <Button variant="outline" size="sm" onclick={refresh} disabled={loading}>
        <RefreshIcon class="h-4 w-4" /> Refresh
      </Button>
      <Button variant="ghost" size="sm" onclick={downloadLogs}>
        <DownloadIcon class="h-4 w-4" /> JSON
      </Button>
    </div>
  </div>

  <!-- Stats summary (worker memory; only when worker is running) -->
  {#if stats}
    <Card>
      <CardHeader>
        <CardTitle>Aggregate stats</CardTitle>
        <CardDescription>From the worker's in-memory log store (resets when worker stops).</CardDescription>
      </CardHeader>
      <CardContent class="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
        <div>
          <div class="text-xs uppercase tracking-wider text-muted-foreground">Assessments</div>
          <div class="mt-1 font-mono text-lg">{stats.total_assessments ?? 0}</div>
        </div>
        <div>
          <div class="text-xs uppercase tracking-wider text-muted-foreground">Audio (s)</div>
          <div class="mt-1 font-mono text-lg">{(stats.total_audio_seconds ?? 0).toFixed(1)}</div>
        </div>
        <div>
          <div class="text-xs uppercase tracking-wider text-muted-foreground">Avg overall</div>
          <div class="mt-1 font-mono text-lg">{Math.round(stats.avg_overall ?? 0)}</div>
        </div>
        <div>
          <div class="text-xs uppercase tracking-wider text-muted-foreground">Avg accuracy</div>
          <div class="mt-1 font-mono text-lg">{Math.round(stats.avg_accuracy ?? 0)}</div>
        </div>
      </CardContent>
    </Card>
  {/if}

  <!-- System log lines (manager + worker parsed entries) -->
  <Card>
    <CardHeader>
      <div class="flex items-center justify-between flex-wrap gap-2">
        <div>
          <CardTitle>System log</CardTitle>
          <CardDescription>
            {#if systemLogs}
              <span class="font-mono">{systemLogs.file}</span>
              · {formatSize(systemLogs.size_bytes)}
              · {systemLogs.lines.length} lines
            {:else}
              Loading…
            {/if}
          </CardDescription>
        </div>
        <div class="flex items-center gap-2 text-xs">
          {#each ['all', 'manager', 'worker', 'system'] as src}
            <Button
              variant={sourceFilter === src ? 'default' : 'outline'}
              size="sm"
              onclick={() => { sourceFilter = src as any; refresh(); }}
            >
              {src}
            </Button>
          {/each}
        </div>
      </div>
    </CardHeader>
    <CardContent>
      {#if systemLogs?.lines.length}
        <div class="max-h-[480px] overflow-y-auto rounded-md bg-crust/60 border border-border p-3 font-mono text-xs leading-relaxed">
          {#each systemLogs.lines as line (line.ts_str + line.msg.slice(0, 20))}
            <div class="py-0.5 hover:bg-surface0/40 rounded px-1 -mx-1">
              <span class="text-muted-foreground">{line.ts_str}</span>
              <Badge variant="outline" class="mx-2 {sourceClass(line.source)}">{line.source}</Badge>
              {#if line.level && line.level !== 'info'}
                <Badge variant="outline" class="mr-2 {levelClass(line.level)}">{line.level}</Badge>
              {/if}
              <span class="whitespace-pre-wrap">{line.msg}</span>
            </div>
          {/each}
        </div>
      {:else}
        <div class="py-12 text-center text-sm text-muted-foreground">
          No log lines yet. Start the worker and use Live Caption / Read Text — log lines will appear here.
        </div>
      {/if}
    </CardContent>
  </Card>

  <!-- Worker stdout (crash output) -->
  <Card>
    <CardHeader>
      <CardTitle>Worker stdout</CardTitle>
      <CardDescription>
        Raw captured console output (logs/worker.out). Import errors, missing
        libraries, and download failures land here before the logger is configured.
      </CardDescription>
    </CardHeader>
    <CardContent>
      {#if workerOutput?.lines.length}
        <div class="max-h-[280px] overflow-y-auto rounded-md bg-crust/60 border border-border p-3 font-mono text-xs leading-relaxed">
          {#each workerOutput.lines as line, i (i)}
            <div class="py-0.5 whitespace-pre-wrap">{line}</div>
          {/each}
        </div>
      {:else}
        <div class="py-8 text-center text-sm text-muted-foreground">
          No worker output captured. The worker hasn't been started or has no errors to report.
        </div>
      {/if}
    </CardContent>
  </Card>

  <!-- Recent practice assessments (worker memory) -->
  <Card>
    <CardHeader>
      <CardTitle>Recent assessments</CardTitle>
      <CardDescription>
        From the worker's in-memory log store (last 50). Resets when the worker is killed.
      </CardDescription>
    </CardHeader>
    <CardContent>
      {#if practiceLogs.length}
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="text-left text-xs uppercase tracking-wider text-muted-foreground border-b border-border">
                <th class="py-2 pr-3">Time</th>
                <th class="py-2 pr-3">Lang</th>
                <th class="py-2 pr-3">Overall</th>
                <th class="py-2 pr-3">Accuracy</th>
                <th class="py-2 pr-3">Fluency</th>
                <th class="py-2 pr-3">Reference</th>
              </tr>
            </thead>
            <tbody>
              {#each practiceLogs.slice().reverse() as log}
                <tr class="border-b border-border/60 hover:bg-surface0/30">
                  <td class="py-2 pr-3 font-mono text-xs text-muted-foreground">{timeOf(log.ts)}</td>
                  <td class="py-2 pr-3 font-mono text-xs">{log.language ?? '—'}</td>
                  <td class="py-2 pr-3 font-mono">
                    <Badge variant="outline" class={levelClass((log.overall ?? 0) >= 80 ? 'info' : (log.overall ?? 0) >= 50 ? 'warning' : 'error')}>
                      {Math.round(log.overall ?? 0)}
                    </Badge>
                  </td>
                  <td class="py-2 pr-3 font-mono">{Math.round(log.subscores?.accuracy ?? 0)}</td>
                  <td class="py-2 pr-3 font-mono">{Math.round(log.subscores?.fluency ?? 0)}</td>
                  <td class="py-2 pr-3 text-xs text-muted-foreground max-w-[280px] truncate">
                    {log.reference ?? '—'}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {:else}
        <div class="py-8 text-center text-sm text-muted-foreground">
          {#if $health?.worker?.state === 'RUNNING'}
            No assessments yet. Use Live Caption or Read Text — your results will appear here.
          {:else}
            Worker is not running. Start it from the top bar to enable live assessments.
          {/if}
        </div>
      {/if}
    </CardContent>
    <CardFooter class="text-xs text-muted-foreground">
      Auto-refresh: {autoRefresh ? 'on (3s)' : 'off'} · Last updated: {new Date().toLocaleTimeString()}
    </CardFooter>
  </Card>
</div>
