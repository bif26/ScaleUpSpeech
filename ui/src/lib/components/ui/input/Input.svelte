<script lang="ts">
  import { cn } from '$lib/utils';

  type Props = {
    value?: string | number;
    class?: string;
    type?: string;
    [k: string]: any;
  };

  let {
    value = $bindable(''),
    class: className = '',
    type = 'text',
    ...rest
  }: Props = $props();
</script>

<input
  {type}
  bind:value
  class={cn(
    'flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50',
    className
  )}
  {...rest}
/>
