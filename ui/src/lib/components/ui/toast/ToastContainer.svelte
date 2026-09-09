<script lang="ts">
  import { toastStore } from './toast-store';
  import { cn } from '$lib/utils';

  const items = $derived($toastStore);
</script>

<div class="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm">
  {#each items as t (t.id)}
    <div
      class={cn(
        'rounded-md border px-4 py-3 text-sm shadow-md backdrop-blur-sm',
        t.kind === 'success' && 'border-success/30 bg-success/10 text-success',
        t.kind === 'error' && 'border-destructive/30 bg-destructive/10 text-destructive',
        t.kind === 'warning' && 'border-warning/30 bg-warning/10 text-warning',
        t.kind === 'default' && 'border-border bg-card text-card-foreground'
      )}
      role="alert"
    >
      {t.message}
    </div>
  {/each}
</div>
