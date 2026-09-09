<script lang="ts">
  import type { HTMLAnchorAttributes, HTMLButtonAttributes } from 'svelte/elements';
  import { buttonVariants, type ButtonSize, type ButtonVariant } from './button-variants';
  import { cn } from '$lib/utils';

  type Props = {
    variant?: ButtonVariant;
    size?: ButtonSize;
    href?: string;
    class?: string;
    children?: import('svelte').Snippet;
  } & HTMLButtonAttributes & HTMLAnchorAttributes;

  let {
    variant = 'default',
    size = 'default',
    href = undefined,
    class: className = '',
    children,
    ...rest
  }: Props = $props();
</script>

{#if href}
  <a {href} class={cn(buttonVariants({ variant, size }), className)} {...rest}>
    {@render children?.()}
  </a>
{:else}
  <button class={cn(buttonVariants({ variant, size }), className)} {...rest}>
    {@render children?.()}
  </button>
{/if}
