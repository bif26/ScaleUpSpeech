<script lang="ts">
  import { badgeVariants, type BadgeVariant } from './badge-variants';
  import { cn } from '$lib/utils';

  type Props = {
    variant?: BadgeVariant;
    class?: string;
    children?: import('svelte').Snippet;
    [k: string]: any;
  };

  let { variant = 'default', class: className = '', children, ...rest }: Props = $props();
</script>

<span class={cn(badgeVariants({ variant }), className)} {...rest}>
  {@render children?.()}
</span>
