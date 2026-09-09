<script lang="ts">
  import { Card, CardHeader, CardTitle, CardContent, CardDescription, CardFooter } from '$lib/components/ui/card';
  import { Button } from '$lib/components/ui/button';
  import { Badge } from '$lib/components/ui/badge';
  import { Separator } from '$lib/components/ui/separator';
  import { health } from '$lib/stores/health';
  import { apiClient } from '$lib/api';
  import { toastStore } from '$lib/components/ui/toast';
  import ArrowRight from 'lucide-svelte/icons/arrow-right';

  async function startWorker() {
    try { await apiClient.startWorker(); toastStore.success('Starting AI worker…'); }
    catch (e: any) { toastStore.error('Failed: ' + e.message); }
  }
</script>

<div class="space-y-6 max-w-4xl">
  <div>
    <h1 class="text-2xl font-semibold tracking-tight">Welcome to LanguageShadow</h1>
    <p class="mt-1 text-sm text-muted-foreground">
      A privacy-first pronunciation coach that runs entirely on your machine.
    </p>
  </div>

  <Card>
    <CardHeader>
      <CardTitle>What it does</CardTitle>
      <CardDescription>
        Listens to your microphone, transcribes with Whisper, and scores your
        pronunciation word-by-word against any reference text — like a typing-test
        site, but for speaking. Everything stays on your computer; no audio leaves
        the machine.
      </CardDescription>
    </CardHeader>
  </Card>

  <Card>
    <CardHeader>
      <CardTitle>Three modes</CardTitle>
    </CardHeader>
    <CardContent class="space-y-3">
      <div>
        <a href="/live" class="font-medium text-lavender hover:underline">Live Caption</a>
        <span class="text-muted-foreground"> — speak freely, see each word turn green / red in real time as you say it.</span>
      </div>
      <div>
        <a href="/read" class="font-medium text-lavender hover:underline">Read Text</a>
        <span class="text-muted-foreground"> — paste a passage from a book, read it aloud, get an overall score plus every wrong word.</span>
      </div>
      <div>
        <a href="/youtube" class="font-medium text-lavender hover:underline">YouTube</a>
        <span class="text-muted-foreground"> — pair your Shadowing browser extension with a YouTube video; the captions become your reading text and you shadow the speaker.</span>
      </div>
    </CardContent>
  </Card>

  <Card>
    <CardHeader>
      <CardTitle>System status</CardTitle>
    </CardHeader>
    <CardContent class="space-y-3 text-sm">
      <div class="flex items-center justify-between">
        <div>
          <span class="font-medium">Manager</span>
          <span class="ml-2 text-muted-foreground">PID {$health?.manager?.pid ?? '—'} · {$health?.manager?.rss_mb ?? 0} MB</span>
        </div>
        <Badge variant="success">running</Badge>
      </div>
      <Separator />
      <div class="flex items-center justify-between">
        <div>
          <span class="font-medium">Worker</span>
          <span class="ml-2 text-muted-foreground">
            PID {$health?.worker?.pid ?? '—'} · {$health?.worker?.rss_mb ?? 0} MB ·
            model {$health?.worker?.model_loaded ? 'loaded' : 'cold'}
          </span>
        </div>
        <Badge variant={$health?.worker?.state === 'RUNNING' ? 'success' : 'outline'}>
          {$health?.worker?.state ?? 'STOPPED'}
        </Badge>
      </div>
      <Separator />
      <div class="flex items-center justify-between">
        <span class="font-medium">Model</span>
        <span class="text-muted-foreground">{$health?.model ?? '—'}</span>
      </div>
    </CardContent>
    <CardFooter>
      <Button onclick={startWorker}>Start AI worker now</Button>
    </CardFooter>
  </Card>

  <Card>
    <CardHeader>
      <CardTitle>RAM budget</CardTitle>
    </CardHeader>
    <CardContent>
      <p class="text-sm text-muted-foreground">
        Designed for an 8 GB Arch + Hyprland machine where the OS and browser
        already use ~2.8 GB. The manager stays under ~50 MB and is always on.
        The AI worker only spins up when you start speaking and is auto-killed
        after <span class="font-mono">{$health?.worker?.idle_timeout ?? 60}s</span>
        of silence.
      </p>
    </CardContent>
  </Card>

  <Card>
    <CardHeader>
      <CardTitle>For extension developers</CardTitle>
    </CardHeader>
    <CardContent>
      <p class="text-sm text-muted-foreground">
        The complete API contract (request / response shapes with examples) is in
        <span class="font-mono">API.md</span> at the repo root. Point your
        existing Shadowing extension at
        <span class="font-mono">POST http://127.0.0.1:8000/api/assess-speech</span>
        and it should work as-is.
      </p>
    </CardContent>
    <CardFooter>
      <Button href="/read" variant="link" class="gap-1 px-0">
        Get started <ArrowRight class="h-4 w-4" />
      </Button>
    </CardFooter>
  </Card>
</div>
