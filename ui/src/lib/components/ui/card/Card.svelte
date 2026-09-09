<script lang="ts">
  import { cn } from '$lib/utils';
  let { class: className = '', children, ...rest }: { class?: string; children?: import('svelte').Snippet; [k: string]: any } = $props();
</script>

<div class={cn('rounded-xl border border-border bg-card text-card-foreground shadow', className)} {...rest}>
  {@render children?.()}
</div>
