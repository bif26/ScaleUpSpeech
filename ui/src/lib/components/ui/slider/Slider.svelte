<script lang="ts">
  // Lightweight Slider wrapping a native <input type="range">.
  // Matches shadcn-svelte's Slider API: { value, min, max, step, onValueChange }.
  import { cn } from '$lib/utils';

  type Props = {
    value?: number;            // current value (single-thumb only)
    min?: number;
    max?: number;
    step?: number;
    class?: string;
    onValueChange?: (v: number) => void;
    [k: string]: any;
  };

  let {
    value = $bindable(0),
    min = 0,
    max = 100,
    step = 1,
    class: className = '',
    onValueChange,
    ...rest
  }: Props = $props();

  function handleChange(e: Event) {
    const v = +(e.currentTarget as HTMLInputElement).value;
    value = v;
    onValueChange?.(v);
  }
</script>

<input
  type="range"
  bind:value
  {min}
  {max}
  {step}
  oninput={handleChange}
  class={cn('w-full', className)}
  {...rest}
/>
