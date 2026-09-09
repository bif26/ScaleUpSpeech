<script lang="ts">
  import { Card, CardHeader, CardTitle, CardContent, CardDescription, CardFooter } from '$lib/components/ui/card';
  import { Button } from '$lib/components/ui/button';
  import { Badge } from '$lib/components/ui/badge';
  import { Separator } from '$lib/components/ui/separator';
  import { toastStore } from '$lib/components/ui/toast';
  import { apiClient } from '$lib/api';
  import { onMount } from 'svelte';
  import YoutubeIcon from 'lucide-svelte/icons/youtube';
  import ExternalLinkIcon from 'lucide-svelte/icons/external-link';

  let captions = $state<any[]>([]);
  let lastTs = $state(0);

  async function pollCaptions() {
    try {
      const r = await apiClient.captions(lastTs);
      if (r.captions?.length) {
        captions = [...captions, ...r.captions].slice(-50);
        lastTs = Math.max(...r.captions.map((c: any) => c.ts));
      }
    } catch { /* ignore */ }
  }

  async function clearCaptions() {
    try {
      await apiClient.clearCaptions();
      captions = [];
      lastTs = Math.floor(Date.now() / 1000);
      toastStore.success('Captions cleared');
    } catch (e: any) {
      toastStore.error('Failed: ' + e.message);
    }
  }

  function buildReference(): string | null {
    if (!captions.length) {
      toastStore.error('No captions captured yet');
      return null;
    }
    return captions.slice(-30).map((c) => c.text).join(' ').replace(/\s+/g, ' ').trim();
  }

  function loadInto(target: 'read' | 'live') {
    const text = buildReference();
    if (!text) return;
    sessionStorage.setItem('ls.reference', text);
    sessionStorage.setItem('ls.target', target);
    location.href = `/${target}`;
  }

  onMount(() => {
    const id = setInterval(pollCaptions, 1500);
    pollCaptions();
    return () => clearInterval(id);
  });
</script>

<div class="space-y-6 max-w-4xl">
  <div>
    <h1 class="text-2xl font-semibold tracking-tight">YouTube Helper</h1>
    <p class="mt-1 text-sm text-muted-foreground">
      Use your existing Shadowing browser extension to capture YouTube captions
      and pipe them to LanguageShadow for pronunciation practice.
    </p>
  </div>

  <Card>
    <CardHeader>
      <CardTitle>How it works</CardTitle>
    </CardHeader>
    <CardContent class="space-y-3 text-sm">
      <ol class="list-decimal pl-5 space-y-2 text-muted-foreground">
        <li>Install your existing Shadowing browser extension (Manifest V3, Chrome/Chromium/Firefox compatible).</li>
        <li>
          Point it at
          <code class="rounded bg-surface0 px-1.5 py-0.5 font-mono text-xs text-foreground">
            POST http://127.0.0.1:8000/api/assess-speech
          </code>
        </li>
        <li>Open any YouTube video with captions and start shadowing.</li>
        <li>The extension sends the caption line + your recording, gets back the score, and renders it in its panel.</li>
      </ol>
    </CardContent>
    <CardFooter>
      <Button href="/API.md" variant="link" target="_blank" class="gap-1 px-0">
        Read the full API contract <ExternalLinkIcon class="h-4 w-4" />
      </Button>
    </CardFooter>
  </Card>

  <Card>
    <CardHeader>
      <CardTitle>Quick API contract</CardTitle>
      <CardDescription>The minimum your extension needs to implement.</CardDescription>
    </CardHeader>
    <CardContent class="space-y-4 text-sm">
      <div>
        <div class="text-[11px] uppercase tracking-wider text-muted-foreground mb-1">Endpoint</div>
        <pre class="rounded-md bg-crust p-3 font-mono text-xs overflow-x-auto">POST http://127.0.0.1:8000/api/assess-speech
Content-Type: application/json</pre>
      </div>

      <div>
        <div class="text-[11px] uppercase tracking-wider text-muted-foreground mb-1">Request body</div>
        <pre class="rounded-md bg-crust p-3 font-mono text-xs overflow-x-auto">{`{
  "audio_base64": "<webm/opus base64>",
  "reference_text": "Ich habe heute einen langen Tag gehabt.",
  "language": "de-DE"
}`}</pre>
      </div>

      <div>
        <div class="text-[11px] uppercase tracking-wider text-muted-foreground mb-1">Response (200 OK)</div>
        <pre class="rounded-md bg-crust p-3 font-mono text-xs overflow-x-auto">{`{
  "status": "OK",
  "overall": 84,
  "subscores": { "accuracy": 88, "fluency": 81, "prosody": 76, "completeness": 100 },
  "pace": { "wpm": 118, "duration_ms": 4200 },
  "words": [
    { "word": "Ich",   "accuracy": 96, "errorType": "None" },
    { "word": "heute", "accuracy": 74, "errorType": "Mispronunciation" },
    { "word": "einen", "accuracy": 0,  "errorType": "Omitted" }
  ],
  "recognized": "ich habe heute langen tag gehabt",
  "cefr": "B2"
}`}</pre>
      </div>

      <div>
        <div class="text-[11px] uppercase tracking-wider text-muted-foreground mb-1">Error response</div>
        <pre class="rounded-md bg-crust p-3 font-mono text-xs overflow-x-auto">{`{ "status": "ERROR", "error": "short human-readable reason" }`}</pre>
      </div>

      <Separator />

      <div class="space-y-1">
        <div class="text-[11px] uppercase tracking-wider text-muted-foreground">Manifest host permission (Manifest V3)</div>
        <pre class="rounded-md bg-crust p-3 font-mono text-xs">{`{
  "host_permissions": ["http://127.0.0.1:8000/*"]
}`}</pre>
        <p class="text-xs text-muted-foreground mt-1">
          CORS is enabled on the worker (<span class="font-mono">Access-Control-Allow-Origin: *</span>),
          so calls from any extension context work.
        </p>
      </div>
    </CardContent>
  </Card>

  <Card>
    <CardHeader>
      <CardTitle>Live captions inbox</CardTitle>
      <CardDescription>
        If your extension posts caption text here (POST /api/caption), this page
        shows them in real time and lets you load them as practice reference.
      </CardDescription>
    </CardHeader>
    <CardContent class="space-y-3">
      <div class="flex items-center justify-between">
        <span class="text-sm text-muted-foreground">
          {captions.length} caption line{captions.length === 1 ? '' : 's'} captured
        </span>
        <Button variant="outline" size="sm" onclick={clearCaptions}>Clear</Button>
      </div>

      <div class="rounded-md border border-border bg-base p-3 max-h-72 overflow-y-auto">
        {#if !captions.length}
          <p class="text-sm text-muted-foreground py-4 text-center">
            No captions captured yet. POST captions to
            <code class="font-mono">/api/caption</code> from your extension.
          </p>
        {:else}
          {#each captions.slice().reverse() as c (c.ts)}
            <div class="flex items-start gap-3 mb-2">
              <span class="font-mono text-[11px] text-muted-foreground min-w-12 pt-0.5">
                {new Date(c.ts * 1000).toLocaleTimeString().slice(0, 8)}
              </span>
              <span class="text-sm">{c.text}</span>
            </div>
          {/each}
        {/if}
      </div>

      {#if captions.length}
        <div class="flex gap-3">
          <Button onclick={() => loadInto('read')}>Load into Read Text</Button>
          <Button variant="outline" onclick={() => loadInto('live')}>Load into Live Caption</Button>
        </div>
      {/if}
    </CardContent>
  </Card>

  <Card>
    <CardHeader>
      <CardTitle>Health check</CardTitle>
    </CardHeader>
    <CardContent>
      <p class="text-sm text-muted-foreground">
        Before your extension sends its first request, ping
        <code class="font-mono">GET http://127.0.0.1:8000/health</code> to make
        sure the worker is up. The first assess request will lazy-load the
        Whisper model (takes 2–4 s); subsequent requests are fast.
      </p>
    </CardContent>
  </Card>
</div>
