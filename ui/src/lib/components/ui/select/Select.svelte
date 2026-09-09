<script lang="ts">
  import { cn } from '$lib/utils';

  type Option = { value: string; label: string };

  type Props = {
    value?: string;            // bound selected value
    options?: Option[];
    class?: string;
    placeholder?: string;
    [k: string]: any;
  };

  let {
    value = $bindable(''),
    options = [],
    class: className = '',
    placeholder = 'Select…',
    ...rest
  }: Props = $props();
</script>

<select
  bind:value
  class={cn(
    'flex h-9 w-full items-center justify-between rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-sm ring-offset-background placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50',
    className
  )}
  {...rest}
>
  {#if placeholder}
    <option value="" disabled>{placeholder}</option>
  {/if}
  {#each options as opt (opt.value)}
    <option value={opt.value}>{opt.label}</option>
  {/each}
</select>
