import { tv, type VariantProps } from 'tailwind-variants';

export const badgeVariants = tv({
  base: 'inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2',
  variants: {
    variant: {
      default:    'border-transparent bg-primary text-primary-foreground hover:bg-primary/80',
      secondary: 'border-transparent bg-secondary text-secondary-foreground hover:bg-secondary/80',
      destructive:'border-transparent bg-destructive text-destructive-foreground hover:bg-destructive/80',
      outline:   'border-border text-foreground',
      success:   'border-transparent bg-success/15 text-success hover:bg-success/25',
      warning:   'border-transparent bg-warning/15 text-warning hover:bg-warning/25',
      danger:    'border-transparent bg-destructive/15 text-destructive hover:bg-destructive/25',
      muted:     'border-transparent bg-muted text-muted-foreground hover:bg-muted/80',
    },
  },
  defaultVariants: {
    variant: 'default',
  },
});

export type BadgeVariant = VariantProps<typeof badgeVariants>['variant'];
