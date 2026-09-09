<script lang="ts">
  import '../app.css';
  import { onMount } from 'svelte';
  import { page } from '$app/stores';
  import { startHealthPolling, stopHealthPolling, health, totalRamMb, workerState } from '$lib/stores/health';
  import { apiClient } from '$lib/api';
  import { Button } from '$lib/components/ui/button';
  import { Badge } from '$lib/components/ui/badge';
  import { ToastContainer, toastStore } from '$lib/components/ui/toast';
  import HomeIcon from 'lucide-svelte/icons/home';
  import MicIcon from 'lucide-svelte/icons/mic';
  import BookIcon from 'lucide-svelte/icons/book-open';
  import YoutubeIcon from 'lucide-svelte/icons/youtube';
  import ActivityIcon from 'lucide-svelte/icons/activity';

  let { children } = $props();

  const nav = [
    { href: '/',         label: 'Home',         icon: HomeIcon },
    { href: '/live',     label: 'Live Caption', icon: MicIcon },
    { href: '/read',     label: 'Read Text',    icon: BookIcon },
    { href: '/youtube', label: 'YouTube',        icon: YoutubeIcon },
  ];

  $effect(() => {
    const path = $page.url.pathname;
    // recompute active when path changes
    void path;
  });

  function isActive(href: string): boolean {
    if (href === '/') return $page.url.pathname === '/';
    return $page.url.pathname.startsWith(href);
  }

  function stateClass(s: string) {
    return s === 'RUNNING' ? 'border-success/40 text-success bg-success/10' :
           s === 'FROZEN'  ? 'border-blue/40 text-blue bg-blue/10' :
           s === 'STARTING' ? 'border-warning/40 text-warning bg-warning/10' :
           'border-destructive/40 text-destructive bg-destructive/10';
  }

  onMount(() => {
    startHealthPolling();
    return () => stopHealthPolling();
  });

  async function startWorker() {
    try { await apiClient.startWorker(); toastStore.success('Starting AI worker…'); }
    catch (e: any) { toastStore.error('Failed: ' + e.message); }
  }
  async function stopWorker() {
    try { await apiClient.stopWorker(); toastStore.success('Worker stopped, RAM freed'); }
    catch (e: any) { toastStore.error('Failed: ' + e.message); }
  }
</script>

<div class="grid h-screen grid-rows-[56px_1fr] grid-cols-[240px_1fr] grid-areas-[header_header/sidebar_main]">
  <!-- Header -->
  <header class="col-span-2 flex items-center justify-between border-b border-border bg-mantle px-5">
    <div class="flex items-center gap-3">
      <span
        class="h-2.5 w-2.5 rounded-full transition-all
        {$health?.worker?.state === 'RUNNING' ? 'bg-success shadow-[0_0_8px] shadow-success' :
         $health?.worker?.state === 'FROZEN'  ? 'bg-blue shadow-[0_0_8px] shadow-blue' :
         'bg-destructive shadow-[0_0_8px] shadow-destructive'}"
      ></span>
      <span class="text-base font-semibold">LanguageShadow</span>
    </div>
    <div class="flex items-center gap-2">
      <span class="font-mono text-xs text-muted-foreground rounded-full border border-border bg-surface0 px-3 py-1">
        {totalRamMb($health)} MB / {$health?.ram_budget_mb ?? '—'} MB
      </span>
      <Button variant="ghost" size="sm" onclick={startWorker}>Start AI</Button>
      <Button variant="ghost" size="sm" onclick={stopWorker}>Stop AI</Button>
    </div>
  </header>

  <!-- Sidebar -->
  <aside class="border-r border-border bg-mantle py-4 overflow-y-auto">
    <nav class="flex flex-col gap-0.5 px-2">
      {#each nav as item (item.href)}
        <a
          href={item.href}
          class="flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors
          {isActive(item.href)
            ? 'bg-surface0 text-foreground border-l-2 border-lavender'
            : 'text-muted-foreground hover:bg-surface0/50 hover:text-foreground'}"
        >
          <item.icon class="h-4 w-4" />
          {item.label}
        </a>
      {/each}
    </nav>

    <div class="mt-6 px-4 text-[11px] uppercase tracking-wider text-muted-foreground">Worker</div>
    <div class="mt-2 px-4 text-xs text-muted-foreground space-y-1">
      <div>
        State:
        <Badge variant="outline" class="ml-1 {stateClass(workerState($health))}">
          {workerState($health)}
        </Badge>
      </div>
      <div>PID {$health?.worker?.pid ?? '—'}</div>
      <div>RAM {$health?.worker?.rss_mb ?? 0} MB</div>
      <div>Model {$health?.worker?.model_loaded ? 'loaded' : 'cold'}</div>
      <div>Idle {$health?.worker?.idle_seconds ?? 0}s / {$health?.worker?.idle_timeout ?? 60}s</div>
    </div>

    <div class="mt-6 px-4 text-[11px] uppercase tracking-wider text-muted-foreground">Model</div>
    <div class="mt-2 px-4 text-xs text-muted-foreground">
      <div>{$health?.model ?? '—'}</div>
    </div>
  </aside>

  <!-- Main content -->
  <main class="overflow-y-auto p-8">
    {@render children?.()}
  </main>
</div>

<ToastContainer />
