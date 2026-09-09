<script lang="ts">
  import { Card, CardHeader, CardTitle, CardContent, CardDescription } from '$lib/components/ui/card';
  import { Button } from '$lib/components/ui/button';
  import { Slider } from '$lib/components/ui/slider';
  import { Select } from '$lib/components/ui/select';
  import { Textarea } from '$lib/components/ui/textarea';
  import { Badge } from '$lib/components/ui/badge';
  import { Separator } from '$lib/components/ui/separator';
  import { toastStore } from '$lib/components/ui/toast';
  import { apiClient } from '$lib/api';
  import { Recorder, TranscribeSession } from '$lib/recorder';
  import type { AssessResponse, IncrementalFrame, WordVerdict } from '$lib/types';
  import { onMount } from 'svelte';

  let reference = $state(`It was the best of times, it was the worst of times, it was the age of wisdom, it was the age of foolishness, it was the epoch of belief, it was the epoch of incredulity.`);
  let language = $state('en');
  let liveMode = $state<'off' | 'on'>('off');
  let targetWpm = $state(110);
  let pauseThr = $state(1.1);

  let recording = $state(false);
  let stateText = $state('Idle');
  let elapsed = $state(0);
  let vuLevel = $state(0);

  let recorder: Recorder | null = null;
  let session: TranscribeSession | null = null;
  let timer: ReturnType<typeof setInterval> | null = null;

  let liveVerdicts = $state<WordVerdict[]>([]);
  let liveSpoken = $state(0);
  let result = $state<AssessResponse | null>(null);
  let recentLogs = $state<any[]>([]);

  const langOptions = [
    { value: 'en', label: 'English' },
    { value: 'es', label: 'Spanish' },
    { value: 'de', label: 'German' },
    { value: 'ar', label: 'Arabic' },
    { value: 'auto', label: 'Auto-detect' },
  ];
  const liveOptions = [
    { value: 'off', label: 'Off (score at end)' },
    { value: 'on',  label: 'On (live score as I speak)' },
  ];

  const refWords = $derived.by(() => {
    return (reference.match(/[A-Za-z0-9']+(?:[-][A-Za-z0-9']+)*/g) || []).map((w) => w.toLowerCase());
  });

  function fmtTime(s: number): string {
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, '0')}`;
  }
  function scoreColor(s: number): string {
    if (s >= 80) return 'text-success';
    if (s >= 60) return 'text-warning';
    return 'text-destructive';
  }
  function cefrClass(level: string): string {
    const v = (level || '').toLowerCase();
    if (v.includes('b2+')) return 'border-success/40 text-success bg-success/10';
    if (v.startsWith('b2')) return 'border-blue/40 text-blue bg-blue/10';
    if (v.startsWith('b1')) return 'border-warning/40 text-warning bg-warning/10';
    if (v.startsWith('a2')) return 'border-destructive/40 text-destructive bg-destructive/10';
    return 'border-destructive/40 text-destructive bg-destructive/15';
  }
  function chipClass(v: WordVerdict): string {
    if (v.errorType === 'Omitted')   return 'border-border bg-muted text-muted-foreground line-through';
    if (v.errorType === 'Inserted') return 'border-warning/40 bg-warning/10 text-warning italic';
    if (v.accuracy >= 80)           return 'border-success/40 bg-success/10 text-success';
    if (v.accuracy >= 60)           return 'border-warning/40 bg-warning/10 text-warning';
    return 'border-destructive/40 bg-destructive/10 text-destructive';
  }

  async function toggle() {
    if (recording) await stop();
    else await start();
  }

  async function start() {
    if (!reference.trim()) {
      toastStore.error('Paste a passage first');
      return;
    }
    result = null;
    liveVerdicts = [];
    liveSpoken = 0;

    try { await apiClient.startWorker(); } catch { /* ignore */ }

    recording = true;
    stateText = 'waiting for AI worker…';

    recorder = new Recorder();
    recorder.onChunk = (pcm) => session?.sendPcm(pcm);
    recorder.onLevel = (rms) => {
      vuLevel = Math.min(100, rms * 300);
    };

    try { await recorder.start(); }
    catch (e: any) {
      toastStore.error('Microphone failed: ' + e.message);
      recording = false;
      return;
    }

    session = new TranscribeSession(
      { referenceText: reference, liveScoring: liveMode === 'on', language },
      {
        onOpen: () => {
          stateText = 'Reading…';
          elapsed = 0;
          timer = setInterval(() => { elapsed += 0.25; }, 250);
        },
        onMessage: (frame) => {
          if (frame.type === 'incremental') {
            const inc = frame as IncrementalFrame;
            liveVerdicts = inc.verdicts;
            liveSpoken = inc.words_spoken;
            stateText = `${inc.words_spoken}/${inc.words_total} words · ${inc.accuracy}%`;
          } else if (frame.type === 'final') {
            handleFinal(frame as AssessResponse);
          }
        },
        onClose: () => {},
        onError: () => {},
      },
    );

    try { await session.open(); }
    catch (e: any) {
      toastStore.error('Connection failed: ' + e.message);
      recording = false;
      await recorder.stop();
      recorder = null;
    }
  }

  async function stop() {
    recording = false;
    stateText = 'Scoring…';
    if (timer) { clearInterval(timer); timer = null; }
    if (session) session.end();
    if (recorder) await recorder.stop();
    recorder = null;
    vuLevel = 0;
  }

  function handleFinal(data: AssessResponse) {
    session?.close();
    session = null;
    stateText = 'Done';
    result = data;
    loadRecent();
  }

  async function loadRecent() {
    try {
      const r = await apiClient.logs(8);
      recentLogs = (r.logs || []).slice().reverse();
    } catch { /* ignore */ }
  }

  onMount(() => {
    if (typeof window !== 'undefined') {
      const preloaded = sessionStorage.getItem('ls.reference');
      if (preloaded) {
        reference = preloaded;
        sessionStorage.removeItem('ls.reference');
        sessionStorage.removeItem('ls.target');
      }
    }
    loadRecent();
    return () => {
      if (timer) clearInterval(timer);
      if (session) try { session.close(); } catch { /* ignore */ }
      if (recorder) try { recorder.stop(); } catch { /* ignore */ }
    };
  });
</script>

<div class="space-y-6 max-w-4xl">
  <div>
    <h1 class="text-2xl font-semibold tracking-tight">Read Text</h1>
    <p class="mt-1 text-sm text-muted-foreground">
      Paste a passage from a book, click Start, and read it aloud. Get your
      score at the end.
    </p>
  </div>

  <Card>
    <CardHeader>
      <CardTitle>Passage to read</CardTitle>
    </CardHeader>
    <CardContent class="space-y-4">
      <Textarea bind:value={reference} rows={6} class="font-mono text-sm"
                placeholder="Paste a passage from a book, news article, or any text…" />
      <div class="flex flex-wrap items-end gap-4">
        <div class="space-y-1.5">
          <label class="text-xs text-muted-foreground">Language</label>
          <Select bind:value={language} options={langOptions} class="w-44" />
        </div>
        <div class="space-y-1.5">
          <label class="text-xs text-muted-foreground">Live scoring</label>
          <Select bind:value={liveMode} options={liveOptions} class="w-56" />
        </div>
        <div class="flex-1"></div>
        <Button variant={recording ? 'destructive' : 'default'} onclick={toggle}>
          {recording ? 'Stop & score' : 'Start reading'}
        </Button>
      </div>
    </CardContent>
  </Card>

  <Card>
    <CardHeader>
      <CardTitle>Settings</CardTitle>
    </CardHeader>
    <CardContent class="space-y-5">
      <div class="space-y-2">
        <div class="flex items-center justify-between">
          <label class="text-sm">Target WPM (min)</label>
          <span class="font-mono text-xs text-muted-foreground">{targetWpm}</span>
        </div>
        <Slider bind:value={targetWpm} min={40} max={180} step={5} />
      </div>
      <div class="space-y-2">
        <div class="flex items-center justify-between">
          <label class="text-sm">Pause threshold (s)</label>
          <span class="font-mono text-xs text-muted-foreground">{pauseThr.toFixed(1)}s</span>
        </div>
        <Slider bind:value={pauseThr} min={0.4} max={2.5} step={0.1} />
      </div>
    </CardContent>
  </Card>

  <div class="flex items-center gap-3 rounded-md border border-border bg-surface0 px-4 py-3">
    <span class="h-3 w-3 rounded-full bg-muted-foreground {recording ? 'bg-destructive ls-pulse' : ''}"></span>
    <span class="font-mono text-sm text-muted-foreground">{fmtTime(elapsed)}</span>
    <div class="flex-1"></div>
    <span class="text-sm text-muted-foreground">{stateText}</span>
    <div class="h-1.5 w-48 rounded-full bg-base overflow-hidden">
      <div class="h-full transition-all"
           style="width: {vuLevel}%; background: linear-gradient(to right, #a6e3a1, #f9e2af, #f38ba8);"></div>
    </div>
  </div>

  <Card>
    <CardHeader>
      <CardTitle>Reading progress</CardTitle>
    </CardHeader>
    <CardContent>
      <div class="flex flex-wrap gap-1.5 min-h-20 rounded-md border border-border bg-base p-4">
        {#if result?.words?.length}
          {#each result.words as w (w.word + Math.random())}
            <span class="rounded-md border px-2.5 py-1 font-mono text-sm {chipClass(w)}"
                  title="{w.errorType} · accuracy {w.accuracy}">{w.word}</span>
          {/each}
        {:else if liveVerdicts.length}
          {#each liveVerdicts as v (v.word + Math.random())}
            <span class="rounded-md border px-2.5 py-1 font-mono text-sm {chipClass(v)}"
                  title="{v.errorType} · accuracy {v.accuracy}">{v.word}</span>
          {/each}
          {#each refWords.slice(liveSpoken) as w (w + Math.random())}
            <span class="rounded-md border border-border bg-muted/30 text-muted-foreground/60 px-2.5 py-1 font-mono text-sm">{w}</span>
          {/each}
        {:else}
          {#each refWords as w (w)}
            <span class="rounded-md border border-border bg-muted/30 text-muted-foreground/60 px-2.5 py-1 font-mono text-sm">{w}</span>
          {/each}
        {/if}
      </div>
    </CardContent>
  </Card>

  {#if result}
    <Card>
      <CardHeader>
        <CardTitle>Result</CardTitle>
      </CardHeader>
      <CardContent class="space-y-5">
        <div class="flex items-center justify-between">
          <div>
            <div class="text-[11px] uppercase tracking-wider text-muted-foreground">Overall</div>
            <div class="font-mono text-4xl font-semibold {scoreColor(result.overall ?? 0)}">
              {result.overall ?? '—'}
            </div>
          </div>
          {#if result.cefr}
            <Badge variant="outline" class="text-base font-bold px-4 py-1.5 {cefrClass(result.cefr)}">
              {result.cefr}
            </Badge>
          {/if}
          <div>
            <div class="text-[11px] uppercase tracking-wider text-muted-foreground">Pace</div>
            <div class="font-mono text-xl">
              {result.pace?.wpm ?? 0} wpm · {fmtTime((result.pace?.duration_ms ?? 0) / 1000)}
            </div>
          </div>
        </div>

        <div class="grid grid-cols-4 gap-3">
          {#each ['accuracy', 'fluency', 'prosody', 'completeness'] as k (k)}
            {@const v = (result.subscores as any)?.[k] ?? 0}
            <div class="rounded-lg border border-border bg-surface0 p-3">
              <div class="text-[11px] uppercase tracking-wider text-muted-foreground">{k}</div>
              <div class="font-mono text-xl font-semibold {scoreColor(v)}">{v}</div>
              <div class="mt-1.5 h-1 rounded-full bg-base overflow-hidden">
                <div class="h-full rounded-full transition-all"
                     style="width: {v}%; background: var(--color-{v >= 80 ? 'success' : v >= 60 ? 'warning' : 'destructive'});">
                </div>
              </div>
            </div>
          {/each}
        </div>

        <Separator />

        <div class="flex flex-wrap gap-3 text-sm">
          <Badge variant="success">matched: {result.n_matched ?? 0}</Badge>
          <Badge variant="danger">mispronounced: {result.n_mispronounced ?? 0}</Badge>
          <Badge variant="warning">inserted: {result.n_inserted ?? 0}</Badge>
          <Badge variant="muted">omitted: {result.n_omitted ?? 0}</Badge>
          <Badge variant="muted">pauses: {result.pause_count ?? 0}</Badge>
        </div>

        {#if result.recognized}
          <div class="text-xs text-muted-foreground">
            <span class="font-mono">Heard:</span> {result.recognized}
          </div>
        {/if}
      </CardContent>
    </Card>
  {/if}

  <Card>
    <CardHeader>
      <CardTitle>Recent attempts</CardTitle>
    </CardHeader>
    <CardContent>
      {#if !recentLogs.length}
        <p class="text-sm text-muted-foreground">No recent attempts.</p>
      {:else}
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="text-[11px] uppercase tracking-wider text-muted-foreground border-b border-border">
                <th class="text-left py-2 pr-4">When</th>
                <th class="text-left py-2 pr-4">Reference</th>
                <th class="text-left py-2 pr-4">Overall</th>
                <th class="text-left py-2 pr-4">CEFR</th>
                <th class="text-left py-2">WPM</th>
              </tr>
            </thead>
            <tbody>
              {#each recentLogs as l (l.ts)}
                <tr class="border-b border-border/50">
                  <td class="py-2 pr-4 text-muted-foreground">{new Date(l.ts * 1000).toLocaleTimeString()}</td>
                  <td class="py-2 pr-4 max-w-xs truncate">{(l.reference || '').slice(0, 60)}…</td>
                  <td class="py-2 pr-4 font-mono font-semibold {scoreColor(l.overall || 0)}">{(l.overall || 0).toFixed(0)}</td>
                  <td class="py-2 pr-4">{l.cefr || '—'}</td>
                  <td class="py-2 font-mono">{l.pace?.wpm || 0}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
    </CardContent>
  </Card>
</div>
