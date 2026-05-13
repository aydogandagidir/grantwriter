import { cn } from '@/lib/utils';

/**
 * Small uppercase pill used to mark module / nav availability state.
 *
 * The four variants mirror the product's lifecycle vocabulary:
 *
 * - ``active``  — shipped, generally available. Subtle blue fill.
 * - ``beta``    — usable but still being hardened; cuts visual weight
 *                 so users notice but don't shy away.
 * - ``soon``    — feature stubbed in the UI for discoverability, not
 *                 yet wired. Renders muted to read as "not available
 *                 yet" without screaming "broken".
 * - ``admin``   — admin-only nav item. Used in the sidebar to hint at
 *                 elevated permissions without hiding the row.
 *
 * The component is purely presentational; gating logic stays in the
 * caller (e.g. sidebar filters ``adminOnly`` items by role).
 */
export type StatusBadgeVariant = 'active' | 'beta' | 'soon' | 'admin';

const VARIANT_CLASSES: Record<StatusBadgeVariant, string> = {
  active:
    'bg-primary/10 text-primary border border-primary/20 ring-1 ring-primary/5',
  beta:
    'bg-blue-500/10 text-blue-600 border border-blue-500/20 dark:text-blue-300',
  soon:
    'bg-muted text-muted-foreground border border-muted-foreground/10',
  admin:
    'bg-amber-500/10 text-amber-700 border border-amber-500/20 dark:text-amber-300',
};

export function StatusBadge({
  variant,
  children,
  className,
}: {
  variant: StatusBadgeVariant;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-md px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider',
        VARIANT_CLASSES[variant],
        className,
      )}
    >
      {children}
    </span>
  );
}
