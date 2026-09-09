<script lang="ts">
  import { Card, CardHeader, CardTitle, CardContent, CardDescription } from '$lib/components/ui/card';
  import { Button } from '$lib/components/ui/button';
  import { Badge } from '$lib/components/ui/badge';
  import { Select } from '$lib/components/ui/select';
  import { toastStore } from '$lib/components/ui/toast';
  import { apiClient } from '$lib/api';
  import { health } from '$lib/stores/health';
  import { Recorder, TranscribeSession } from '$lib/recorder';
  import type { PartialFrame } from '$lib/types';
  import { onMount } from 'svelte';
  import CopyIcon from 'lucide-svelte/icons/copy';
  import DownloadIcon from 'lucide-svelte/icons/download';
  import TrashIcon from 'lucide-svelte/icons/trash-2';
  import MicIcon from 'lucide-svelte/icons/mic';

  let language = $state('en');
  let autoStart = $state(true);

  let recording = $state(false);
  let connecting = $state(false);
  let stateText = $state('Idle');
  let elapsed = $state(0);
  let vuLevel = $state(0);

  /* partial = what whisper currently hears (kept volatile); transcript =
     everything finalized so far (final frames appended on Stop). */
  let partialText = $state('');
  let transcript = $state('');
  let wordCount = $state(0);

  let recorder: Recorder | null = null;
  let session: TranscribeSession | null = null;
  let timer: ReturnType<typeof setInterval> | null = null;

  const langOptions = [
    { value: 'en', label: 'English' },
    { value: 'es', label: 'Spanish' },
    { value: 'de', label: 'German' },
    { value: 'fr', label: 'French' },
    { value: 'ar', label: 'Arabic' },
    { value: 'auto', label: 'Auto-detect' },
  ];

  const workerUp = $derived(health()?.worker?.state === 'RUNNING');

  function fmtTime(s: number): string {
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, '0')}`;
  }

  function countWords(t: string): number {
    return t.trim() ? t.trim().split(/\s+/).length : 0;
  }

  function pushTranscript(text: string) {
    if (!text.trim()) return;
    transcript = transcript ? transcript.trimEnd() + ' ' + text.trim() : text.trim();
    wordCount = countWords(transcript);
  }

  async function toggle() {
    if (recording || connecting) await stop();
    else await start();
  }

  async function start() {
    partialText = '';
    connecting = true;
    stateText = workerUp ? 'connecting…' : 'waking AI worker…';

    // Auto-link with the model: ask the manager to start the worker now, so
    // the model loads while the browser asks for mic permission.
    if (autoStart) {
      try { await apiClient.startWorker(); } catch { /* the WS proxy retries anyway */ }
    }

    recorder = new Recorder();
    recorder.onChunk = (pcm) => session?.sendPcm(pcm);
    recorder.onLevel = (rms) => { vuLevel = Math.min(100, rms * 300); };

    try { await recorder.start(); }
    catch (e: any) {
      toastStore.error('Microphone failed: ' + e.message);
      connecting = false;
      recorder = null;
      return;
    }

    session = new TranscribeSession(
      { referenceText: '', liveScoring: false, language },
      {
        onOpen: () => {
          connecting = false;
          recording = true;
          stateText = 'Listening…';
          elapsed = 0;
          timer = setInterval(() => { elapsed += 0.25; }, 250);
        },
        onMessage: (frame) => {
          if (frame.type === 'partial') {
            const p = frame as PartialFrame;
            partialText = p.text;
            stateText = 'Listening…';
          } else if (frame.type === 'final') {
            const f: any = frame;
            partialText = '';
            if (f.recognized) pushTranscript(f.recognized);
            stateText = 'Paused';
          }
        },
        onClose: () => {
          if (recording || connecting) {
            recording = false;
            connecting = false;
            stateText = 'Disconnected';
          }
        },
        onError: () => {},
      },
    );

    try { await session.open(); }
    catch (e: any) {
      toastStore.error('Connection failed: ' + (e?.message || 'worker unreachable'));
      recording = false;
      connecting = false;
      await recorder.stop();
      recorder = null;
    }
  }

  async function stop() {
    if (timer) { clearInterval(timer); timer = null; }
    stateText = 'Finishing…';
    // Send END so the worker flushes its last partial words back as a final
    // frame; the transcript is updated from that frame's `recognized` text.
    const pending = session;
    const rec = recorder;
    session = null;
    recorder = null;
    recording = false;
    connecting = false;
    vuLevel = 0;
    rec?.stop();
    if (pending) {
      pending.end();
      // Give the final frame a moment to arrive before we declare done.
      setTimeout(() => {
        if (!recording) stateText = transcript ? 'Paused' : 'Idle';
      }, 800);
    } else {
      stateText = transcript ? 'Paused' : 'Idle';
    }
  }

  function clearTranscript() {
    if (recording) {
      toastStore.error('Stop the captioning first');
      return;
    }
    transcript = '';
    partialText = '';
    wordCount = 0;
    elapsed = 0;
    stateText = 'Idle';
  }

  function joinAll(): string {
    return (transcript + (partialText ? (transcript ? ' ' : '') + partialText : '')).trim();
  }

  async function copyTranscript() {
    const all = joinAll();
    if (!all) { toastStore.error('Nothing to copy yet'); return; }
    try {
      await navigator.clipboard.writeText(all);
      toastStore.success('Transcript copied');
    } catch {
      toastStore.error('Clipboard blocked by the browser');
    }
  }

  function downloadTranscript() {
    const all = joinAll();
    if (!all) { toastStore.error('Nothing to save yet'); return; }
    const blob = new Blob([all], { type: 'text/plain;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `caption-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.txt`;
    a.click();
    URL.revokeObjectURL(a.href);
    toastStore.success('Transcript saved');
  }

  onMount(() => {
    // Auto-link with the model: pre-warm the worker as soon as the page opens
    // so the first Start click is instant.
    if (autoStart) {
      apiClient.startWorker().catch(() => { /* manager will retry on WS */ });
    }
    return () => {
      if (timer) clearInterval(timer);
      if (session) try { session.close(); } catch { /* ignore */ }
      if (recorder) try { recorder.stop(); } catch { /* ignore */ }
    };
  });
</script>

<div class="space-y-6 max-w-4xl">
  <div>
    <h1 class="text-2xl font-semibold tracking-tight">Live Caption</h1>
    <p class="mt-1 text-sm text-muted-foreground">
      Speak freely — Whisper transcribes every word in real time. No reference
      text, no scoring: just live captions of everything you say.
    </p>
  </div>

  <Card>
    <CardHeader>
      <CardTitle>Session</CardTitle>
      <CardDescription>
        The AI worker starts automatically and the model loads on demand —
        the first words may take a few seconds while it wakes up.
      </CardDescription>
    </CardHeader>
    <CardContent class="space-y-4">
      <div class="flex flex-wrap items-end gap-4">
        <div class="space-y-1.5">
          <label for="live-language" class="text-xs text-muted-foreground">Language</label>
          <Select id="live-language" bind:value={language} options={langOptions} class="w-44" disabled={recording || connecting} />
        </div>
        <label class="flex items-center gap-2 text-sm text-muted-foreground pb-2 cursor-pointer select-none">
          <input type="checkbox" bind:checked={autoStart}
                 class="h-4 w-4 rounded border-border accent-lavender" />
          Auto-start AI
        </label>
        <div class="flex-1"></div>
        <Button variant={recording || connecting ? 'destructive' : 'default'} onclick={toggle} class="gap-2">
          <MicIcon class="h-4 w-4" />
          {recording ? 'Stop' : connecting ? 'Cancel' : 'Start captioning'}
        </Button>
      </div>

      <div class="flex items-center gap-3 rounded-md border border-border bg-surface0 px-4 py-3">
        <span class="h-3 w-3 rounded-full bg-muted-foreground {recording ? 'bg-destructive ls-pulse' : ''}"></span>
        <span class="font-mono text-sm text-muted-foreground">{fmtTime(elapsed)}</span>
        <div class="flex-1"></div>
        <span class="text-sm text-muted-foreground">
          {#if connecting}
            {stateText} · model {health?.worker?.model_loaded ? 'loaded' : 'loading…'}
          {:else}
            {stateText}
          {/if}
        </span>
        <div class="h-1.5 w-48 rounded-full bg-base overflow-hidden">
          <div class="h-full transition-all"
               style="width: {vuLevel}%; background: linear-gradient(to right, #a6e3a1, #f9e2af, #f38ba8);"></div>
        </div>
      </div>
    </CardContent>
  </Card>

  <Card>
    <CardHeader>
      <CardTitle>Live caption</CardTitle>
      {#if recording}
        <Badge variant="success" class="ml-auto">live</Badge>
      {:else if connecting}
        <Badge variant="warning" class="ml-auto">connecting</Badge>
      {/if}
    </CardHeader>
    <CardContent>
      <div class="min-h-40 rounded-md border border-border bg-base p-4">
        {#if partialText || transcript}
          <p class="text-lg leading-relaxed">
            <span class="text-foreground">{transcript}{transcript && partialText ? ' ' : ''}</span>
            {#if partialText}<span class="text-lavender animate-pulse">{partialText}</span>{/if}
          </p>
        {:else}
          <p class="text-sm text-muted-foreground">
            {recording || connecting
              ? 'Listening… start speaking and the words appear here.'
              : 'Press “Start captioning” and speak — your words appear here in real time.'}
          </p>
        {/if}
      </div>
      <div class="mt-3 flex flex-wrap items-center gap-2">
        <Badge variant="muted">{wordCount + countWords(partialText)} words</Badge>
        <Badge variant="muted">{fmtTime(elapsed)}</Badge>
        {#if elapsed > 4}
          <Badge variant="muted">{Math.round((wordCount + countWords(partialText)) / Math.max(elapsed, 1) * 60)} wpm</Badge>
        {/if}
        <div class="flex-1"></div>
        <Button variant="outline" size="sm" class="gap-1.5" onclick={copyTranscript}>
          <CopyIcon class="h-3.5 w-3.5" /> Copy
        </Button>
        <Button variant="outline" size="sm" class="gap-1.5" onclick={downloadTranscript}>
          <DownloadIcon class="h-3.5 w-3.5" /> Save .txt
        </Button>
        <Button variant="outline" size="sm" class="gap-1.5" onclick={clearTranscript}>
          <TrashIcon class="h-3.5 w-3.5" /> Clear
        </Button>
      </div>
    </CardContent>
  </Card>

  <Card>
    <CardHeader>
      <CardTitle>Tips</CardTitle>
    </CardHeader>
    <CardContent class="text-sm text-muted-foreground space-y-2">
      <p>
        The worker stays hot for <span class="font-mono">{health?.worker?.idle_timeout ?? 60}s</span>
        after you stop speaking, then the manager kills it to free RAM — so you
        can pause for a coffee; only the in-progress partial line is lost.
      </p>
      <p>
        Want scoring instead? Paste the text into
        <a href="/read" class="text-lavender hover:underline">Read Text</a> and
        read it aloud — every word gets graded against the reference.
      </p>
    </CardContent>
  </Card>
</div>
