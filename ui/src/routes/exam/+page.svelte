<script lang="ts">
  import { Card, CardHeader, CardTitle, CardContent, CardDescription } from '$lib/components/ui/card';
  import { Button } from '$lib/components/ui/button';
  import { Badge } from '$lib/components/ui/badge';
  import { Separator } from '$lib/components/ui/separator';
  import { toastStore } from '$lib/components/ui/toast';
  import { apiClient } from '$lib/api';
  import { Recorder, TranscribeSession } from '$lib/recorder';
  import type { ExamExportResponse, ExamFinalFrame, ExamTask } from '$lib/types';
  import { onMount } from 'svelte';
  import GraduationCapIcon from 'lucide-svelte/icons/graduation-cap';
  import MicIcon from 'lucide-svelte/icons/mic';
  import SquareIcon from 'lucide-svelte/icons/square';
  import CopyIcon from 'lucide-svelte/icons/copy';
  import CheckIcon from 'lucide-svelte/icons/check';
  import DownloadIcon from 'lucide-svelte/icons/download';
  import RotateCcwIcon from 'lucide-svelte/icons/rotate-ccw';
  import ArrowLeftIcon from 'lucide-svelte/icons/arrow-left';
  import ClockIcon from 'lucide-svelte/icons/clock';
  import ListChecksIcon from 'lucide-svelte/icons/list-checks';
  import FileTextIcon from 'lucide-svelte/icons/file-text';
  import LightbulbIcon from 'lucide-svelte/icons/lightbulb';

  type Phase = 'pick' | 'prep' | 'record' | 'analyzing' | 'result';

  const LEVELS = ['A2', 'B1', 'B2'] as const;

  // ---- task library -----------------------------------------------------
  let tasks = $state<ExamTask[]>([]);
  let loadingTasks = $state(true);
  let selectedLevel = $state<string>('B1');
  let selectedTask = $state<ExamTask | null>(null);

  const tasksForLevel = $derived(tasks.filter((t) => t.level === selectedLevel));

  // ---- session state ----------------------------------------------------
  let phase = $state<Phase>('pick');
  let prepLeft = $state(0);
  let elapsed = $state(0);
  let vuLevel = $state(0);
  let partialText = $state('');

  let recorder: Recorder | null = null;
  let session: TranscribeSession | null = null;
  let tickTimer: ReturnType<typeof setInterval> | null = null;
  let prepTimer: ReturnType<typeof setInterval> | null = null;
  let guardTimer: ReturnType<typeof setTimeout> | null = null;

  // ---- result / export ---------------------------------------------------
  let result = $state<ExamFinalFrame | null>(null);
  let exporting = $state(false);
  let assessment = $state<ExamExportResponse | null>(null);
  let copied = $state(false);

  const remaining = $derived(
    selectedTask ? Math.max(0, selectedTask.speak_seconds - elapsed) : 0
  );

  function fmtTime(s: number): string {
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, '0')}`;
  }

  function evClass(et: string): string {
    if (et === 'ok') return 'border-success/40 bg-success/10 text-success';
    if (et === 'unclear') return 'border-warning/40 bg-warning/10 text-warning';
    return 'border-destructive/40 bg-destructive/10 text-destructive';
  }

  // ---- lifecycle ---------------------------------------------------------
  onMount(() => {
    loadTasks();
    return () => cleanup();
  });

  async function loadTasks() {
    loadingTasks = true;
    try {
      const r = await apiClient.examTasks();
      tasks = r.tasks || [];
      // start on the first level that actually has tasks
      if (tasks.length && !tasks.some((t) => t.level === selectedLevel)) {
        selectedLevel = tasks[0].level;
      }
    } catch (e: any) {
      toastStore.error('Could not load the task library: ' + e.message);
    } finally {
      loadingTasks = false;
    }
  }

  function cleanup() {
    if (tickTimer) { clearInterval(tickTimer); tickTimer = null; }
    if (prepTimer) { clearInterval(prepTimer); prepTimer = null; }
    if (guardTimer) { clearTimeout(guardTimer); guardTimer = null; }
    try { session?.close(); } catch { /* ignore */ }
    session = null;
    try { recorder?.stop(); } catch { /* ignore */ }
    recorder = null;
  }

  // ---- phase transitions -------------------------------------------------
  function pickTask(t: ExamTask) {
    cleanup();
    selectedTask = t;
    result = null;
    assessment = null;
    partialText = '';
    elapsed = 0;
    startPrep();
  }

  function startPrep() {
    phase = 'prep';
    prepLeft = selectedTask?.prep_seconds ?? 30;
    if (prepTimer) clearInterval(prepTimer);
    prepTimer = setInterval(() => {
      prepLeft -= 1;
      if (prepLeft <= 0) {
        if (prepTimer) { clearInterval(prepTimer); prepTimer = null; }
        beginRecording();
      }
    }, 1000);
  }

  function skipPrep() {
    if (prepTimer) { clearInterval(prepTimer); prepTimer = null; }
    beginRecording();
  }

  async function beginRecording() {
    if (!selectedTask) return;
    // Defensively stop the prep countdown (it auto-fires this function).
    if (prepTimer) { clearInterval(prepTimer); prepTimer = null; }
    phase = 'record';
    result = null;
    assessment = null;
    partialText = '';
    elapsed = 0;
    vuLevel = 0;

    try { await apiClient.startWorker(); } catch { /* ignore */ }

    recorder = new Recorder();
    recorder.onChunk = (pcm) => session?.sendPcm(pcm);
    recorder.onLevel = (rms) => { vuLevel = Math.min(100, rms * 300); };

    try { await recorder.start(); }
    catch (e: any) {
      toastStore.error('Microphone failed: ' + e.message);
      phase = 'prep';
      return;
    }

    session = new TranscribeSession(
      {
        referenceText: '',
        liveScoring: false,
        language: selectedTask.language || 'de-DE',
        mode: 'exam',
      },
      {
        onOpen: () => {
          if (tickTimer) clearInterval(tickTimer);
          tickTimer = setInterval(() => {
            elapsed += 0.25;
            // Real exams cut you off — so do we (auto-stop at speak_seconds).
            if (selectedTask && elapsed >= selectedTask.speak_seconds + 1) {
              stopRecording();
            }
          }, 250);
        },
        onMessage: (frame: any) => {
          if (frame.type === 'partial') {
            partialText = frame.text as string;
          } else if (frame.type === 'final') {
            handleFinal(frame as ExamFinalFrame);
          }
        },
        onClose: () => {
          if (tickTimer) { clearInterval(tickTimer); tickTimer = null; }
          if (phase === 'record' || phase === 'analyzing') {
            phase = 'prep';
            toastStore.error('Connection lost — press Start speaking to try again');
          }
        },
        onError: () => {},
      },
    );

    try { await session.open(); }
    catch (e: any) {
      toastStore.error('Connection to the AI worker failed: ' + e.message);
      phase = 'prep';
      await recorder.stop();
      recorder = null;
    }
  }

  async function stopRecording() {
    if (phase !== 'record') return; // guard double-stop (timer + button)
    phase = 'analyzing';
    if (tickTimer) { clearInterval(tickTimer); tickTimer = null; }
    session?.end();
    try { await recorder?.stop(); } catch { /* ignore */ }
    recorder = null;
    vuLevel = 0;
    // The worker transcribes the remaining tail before answering. Never let
    // the user hang forever if something goes wrong.
    if (guardTimer) clearTimeout(guardTimer);
    guardTimer = setTimeout(() => {
      if (phase === 'analyzing') {
        phase = 'prep';
        toastStore.error('Analysis timed out — the AI worker did not respond');
      }
    }, 120000);
  }

  function handleFinal(frame: ExamFinalFrame) {
    if (guardTimer) { clearTimeout(guardTimer); guardTimer = null; }
    session?.close();
    session = null;
    if (frame.status && frame.status !== 'OK') {
      phase = 'prep';
      toastStore.error(frame.error || 'No usable audio recorded');
      return;
    }
    phase = 'result';
    result = frame;
  }

  // ---- export ------------------------------------------------------------
  async function exportAssessment() {
    if (!result || !selectedTask || exporting) return;
    exporting = true;
    try {
      const r = await apiClient.examExport({
        level: selectedTask.level,
        slug: selectedTask.slug,
        transcript: result.recognized || '',
        words: result.words || [],
        metrics: result.metrics,
        duration_s: result.duration_s,
      });
      if (r.error) throw new Error(r.error);
      assessment = r;
      copied = false;
    } catch (e: any) {
      toastStore.error('Export failed: ' + e.message);
    } finally {
      exporting = false;
    }
  }

  async function copyAssessment() {
    if (!assessment) return;
    try {
      await navigator.clipboard.writeText(assessment.markdown);
      copied = true;
      toastStore.success('Assessment file copied — paste it into any LLM');
      setTimeout(() => { copied = false; }, 4000);
    } catch {
      toastStore.error('Copy failed — select the text manually and copy it');
    }
  }

  function downloadAssessment() {
    if (!assessment) return;
    const blob = new Blob([assessment.markdown], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = assessment.filename || 'assessment.md';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  function tryAgain() {
    if (!selectedTask) { phase = 'pick'; return; }
    cleanup();
    result = null;
    assessment = null;
    partialText = '';
    elapsed = 0;
    startPrep();
  }

  function backToTasks() {
    cleanup();
    selectedTask = null;
    result = null;
    assessment = null;
    phase = 'pick';
  }
</script>

<div class="space-y-6 max-w-4xl">
  <div class="flex items-start justify-between gap-4">
    <div>
      <h1 class="flex items-center gap-2 text-2xl font-semibold tracking-tight">
        <GraduationCapIcon class="h-6 w-6 text-lavender" />
        Exam Trainer
      </h1>
      <p class="mt-1 text-sm text-muted-foreground">
        German speaking practice in the Goethe / ÖSD / telc format (A2–B2).
        Speak, then export one file — paste it into any LLM and get an
        official-style CEFR score out of 100.
      </p>
    </div>
    {#if phase !== 'pick'}
      <Button variant="ghost" size="sm" onclick={backToTasks} class="gap-1.5">
        <ArrowLeftIcon class="h-4 w-4" /> All tasks
      </Button>
    {/if}
  </div>

  <!-- ================= PICK ================= -->
  {#if phase === 'pick'}
    <Card>
      <CardHeader>
        <CardTitle>How it works</CardTitle>
        <CardDescription>
          1 · Pick a level and a task — 2 · read the situation and speak —
          3 · export the assessment file — 4 · paste it into ChatGPT, Claude,
          Gemini or any local LLM to get your score. No API key needed.
        </CardDescription>
      </CardHeader>
    </Card>

    <div class="flex items-center gap-2">
      {#each LEVELS as lv (lv)}
        <Button
          variant={selectedLevel === lv ? 'default' : 'outline'}
          size="sm"
          onclick={() => { selectedLevel = lv; }}
        >
          {lv}
          <span class="ml-1 font-mono text-xs opacity-70">
            {tasks.filter((t) => t.level === lv).length}
          </span>
        </Button>
      {/each}
      <div class="flex-1"></div>
      <span class="text-xs text-muted-foreground">
        Tasks live in <code class="font-mono">tasks/{selectedLevel}/</code> — drop a
        <code class="font-mono">.md</code> file there to add more.
      </span>
    </div>

    {#if loadingTasks}
      <Card><CardContent class="py-8 text-center text-sm text-muted-foreground">Loading tasks…</CardContent></Card>
    {:else if tasksForLevel.length === 0}
      <Card>
        <CardContent class="py-8 text-center space-y-2">
          <p class="text-sm text-muted-foreground">No tasks for {selectedLevel} yet.</p>
          <p class="text-xs text-muted-foreground">
            Add one: create <code class="font-mono">tasks/{selectedLevel}/01_mein_thema.md</code>
            following <code class="font-mono">tasks/README.md</code> — it appears here instantly.
          </p>
        </CardContent>
      </Card>
    {:else}
      <div class="grid gap-3 sm:grid-cols-2">
        {#each tasksForLevel as t (t.level + t.slug)}
          <Card class="transition-colors hover:border-lavender/50">
            <CardHeader class="pb-2">
              <CardTitle class="text-base">{t.title}</CardTitle>
              <CardDescription class="flex flex-wrap items-center gap-1.5 pt-1">
                <Badge variant="outline">{t.exam || t.level}</Badge>
                {#if t.task_type}<Badge variant="muted">{t.task_type}</Badge>{/if}
              </CardDescription>
            </CardHeader>
            <CardContent class="space-y-3">
              <p class="line-clamp-2 text-sm text-muted-foreground">
                {t.situation.split('\n')[0]}
              </p>
              <div class="flex items-center justify-between">
                <span class="flex items-center gap-1 text-xs text-muted-foreground">
                  <ClockIcon class="h-3.5 w-3.5" />
                  {t.prep_seconds}s prep · {Math.round(t.speak_seconds / 60) || 1} min speak
                </span>
                <Button size="sm" onclick={() => pickTask(t)}>Start</Button>
              </div>
            </CardContent>
          </Card>
        {/each}
      </div>
    {/if}
  {/if}

  <!-- ================= PREP ================= -->
  {#if phase === 'prep' && selectedTask}
    <Card>
      <CardHeader>
        <CardTitle class="flex items-center justify-between">
          <span>{selectedTask.title}</span>
          <Badge variant="outline">{selectedTask.exam || selectedTask.level}</Badge>
        </CardTitle>
        {#if selectedTask.exam_source}
          <CardDescription>{selectedTask.exam_source}</CardDescription>
        {/if}
      </CardHeader>
      <CardContent class="space-y-5">
        <div>
          <div class="mb-1 text-[11px] uppercase tracking-wider text-muted-foreground">Situation</div>
          <p class="whitespace-pre-line rounded-md border border-border bg-base p-4 text-sm leading-relaxed">
            {selectedTask.situation}
          </p>
        </div>
        {#if selectedTask.requirements.length}
          <div>
            <div class="mb-1.5 flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-muted-foreground">
              <ListChecksIcon class="h-3.5 w-3.5" /> Address all of these points
            </div>
            <ul class="space-y-1 text-sm">
              {#each selectedTask.requirements as r, i (i)}
                <li class="flex gap-2"><span class="text-lavender">•</span><span>{r}</span></li>
              {/each}
            </ul>
          </div>
        {/if}
        <div class="flex items-center gap-4 rounded-md border border-border bg-surface0 px-4 py-3">
          <ClockIcon class="h-5 w-5 text-warning" />
          <span class="text-sm">Preparation time:</span>
          <span class="font-mono text-2xl font-semibold tabular-nums">{fmtTime(Math.max(0, prepLeft))}</span>
          <div class="flex-1"></div>
          <Button onclick={skipPrep} class="gap-1.5">
            <MicIcon class="h-4 w-4" /> Start speaking now
          </Button>
        </div>
      </CardContent>
    </Card>
  {/if}

  <!-- ================= RECORD ================= -->
  {#if phase === 'record' && selectedTask}
    <Card>
      <CardHeader>
        <CardTitle>{selectedTask.title}</CardTitle>
        <CardDescription>Speak German — address every required point.</CardDescription>
      </CardHeader>
      <CardContent class="space-y-4">
        <div class="flex items-center gap-3 rounded-md border border-border bg-surface0 px-4 py-3">
          <span class="h-3 w-3 rounded-full bg-destructive ls-pulse"></span>
          <span class="font-mono text-sm">{fmtTime(elapsed)}</span>
          <span class="font-mono text-xs text-muted-foreground">
            / {fmtTime(selectedTask.speak_seconds)} (auto-stop {fmtTime(remaining)})
          </span>
          <div class="flex-1"></div>
          <div class="h-1.5 w-40 rounded-full bg-base overflow-hidden">
            <div class="h-full transition-all"
                 style="width: {vuLevel}%; background: linear-gradient(to right, #a6e3a1, #f9e2af, #f38ba8);">
            </div>
          </div>
          <Button variant="destructive" onclick={stopRecording} class="gap-1.5">
            <SquareIcon class="h-4 w-4" /> Stop
          </Button>
        </div>
        <div class="min-h-28 rounded-md border border-border bg-base p-4 text-sm leading-relaxed">
          {#if partialText}
            <span class="text-muted-foreground">{partialText}</span><span class="animate-pulse text-lavender">▍</span>
          {:else}
            <span class="text-muted-foreground/60">
              Live transcript preview appears here while you speak… (analysis is only computed after Stop)
            </span>
          {/if}
        </div>
      </CardContent>
    </Card>
  {/if}

  <!-- ================= ANALYZING ================= -->
  {#if phase === 'analyzing'}
    <Card>
      <CardContent class="flex flex-col items-center gap-3 py-12">
        <div class="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-lavender"></div>
        <p class="text-sm font-medium">Transcribing and measuring your answer…</p>
        <p class="text-xs text-muted-foreground">
          This can take up to a minute on CPU — the whole recording is checked word by word.
        </p>
      </CardContent>
    </Card>
  {/if}

  <!-- ================= RESULT ================= -->
  {#if phase === 'result' && result && selectedTask}
    <Card>
      <CardHeader>
        <CardTitle>Your answer</CardTitle>
        <CardDescription>
          {selectedTask.title} · {selectedTask.exam || selectedTask.level}
        </CardDescription>
      </CardHeader>
      <CardContent class="space-y-5">
        <p class="whitespace-pre-line rounded-md border border-border bg-base p-4 text-sm leading-relaxed">
          {result.recognized}
        </p>

        {#if result.metrics}
          <div class="grid grid-cols-3 gap-3 sm:grid-cols-6">
            {#each [
              { label: 'words', value: result.metrics.word_count },
              { label: 'wpm (net)', value: result.metrics.wpm_net },
              { label: 'duration', value: fmtTime(result.metrics.duration_s || 0) },
              { label: 'pauses >0.5s', value: result.metrics.pause_count },
              { label: 'longest', value: result.metrics.longest_pause_s + 's' },
              { label: 'fillers', value: result.metrics.filler_count },
            ] as m, i (i)}
              <div class="rounded-lg border border-border bg-surface0 p-3 text-center">
                <div class="text-[10px] uppercase tracking-wider text-muted-foreground">{m.label}</div>
                <div class="font-mono text-lg font-semibold">{m.value}</div>
              </div>
            {/each}
          </div>
        {/if}

        {#if result.evidence?.words?.length}
          <div>
            <div class="mb-1.5 text-[11px] uppercase tracking-wider text-muted-foreground">
              Words the recogniser was unsure about
            </div>
            <div class="flex max-h-40 flex-wrap gap-1.5 overflow-y-auto rounded-md border border-border bg-base p-3">
              {#each result.evidence.words.filter((w) => w.error_type !== 'ok') as w, i (i)}
                <span class="rounded-md border px-2 py-0.5 font-mono text-xs {evClass(w.error_type)}"
                      title="accuracy {w.accuracy} · {w.note || w.error_type}">
                  {w.word} <span class="opacity-60">{w.accuracy.toFixed(2)}</span>
                </span>
              {/each}
              {#if result.evidence.words.filter((w) => w.error_type !== 'ok').length === 0}
                <span class="text-xs text-success">Every word recognised with high confidence — well spoken!</span>
              {/if}
            </div>
          </div>
        {/if}

        <Separator />

        <div class="flex flex-wrap gap-2">
          <Button onclick={exportAssessment} disabled={exporting} class="gap-1.5">
            <FileTextIcon class="h-4 w-4" />
            {exporting ? 'Building file…' : 'Export assessment file'}
          </Button>
          <Button variant="outline" onclick={tryAgain} class="gap-1.5">
            <RotateCcwIcon class="h-4 w-4" /> Try again
          </Button>
          <Button variant="ghost" onclick={backToTasks} class="gap-1.5">
            <ArrowLeftIcon class="h-4 w-4" /> Choose another task
          </Button>
        </div>
      </CardContent>
    </Card>

    {#if assessment}
      <Card>
        <CardHeader>
          <CardTitle>Assessment file (for any LLM)</CardTitle>
          <CardDescription class="flex items-center gap-1.5">
            <LightbulbIcon class="h-3.5 w-3.5 text-warning" />
            Copy everything below and paste it into ChatGPT / Claude / Gemini —
            you get a 100-point CEFR score, per-criterion feedback and a German summary.
          </CardDescription>
        </CardHeader>
        <CardContent class="space-y-3">
          <div class="flex flex-wrap items-center gap-2">
            <Button variant={copied ? 'outline' : 'default'} onclick={copyAssessment} class="gap-1.5">
              {#if copied}<CheckIcon class="h-4 w-4 text-success" /> Copied
              {:else}<CopyIcon class="h-4 w-4" /> Copy file{/if}
            </Button>
            <Button variant="outline" onclick={downloadAssessment} class="gap-1.5">
              <DownloadIcon class="h-4 w-4" /> Download .md
            </Button>
            <span class="font-mono text-xs text-muted-foreground">{assessment.filename}</span>
          </div>
          <textarea
            readonly
            rows={18}
            class="w-full rounded-md border border-border bg-base p-4 font-mono text-xs leading-relaxed text-muted-foreground focus:outline-none"
            >{assessment.markdown}</textarea>
        </CardContent>
      </Card>
    {/if}
  {/if}
</div>
