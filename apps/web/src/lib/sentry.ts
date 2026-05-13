/**
 * Frontend Sentry error-tracking integration.
 *
 * Mirrors the backend pattern (src/core/observability.py): the SDK is
 * optional — when `NEXT_PUBLIC_SENTRY_DSN` is missing, all functions
 * become safe no-ops.
 *
 * ## Setup
 *
 * 1. `pnpm add @sentry/nextjs` (done — see apps/web/package.json)
 * 2. Set `NEXT_PUBLIC_SENTRY_DSN` in .env.local / deploy secrets
 * 3. Call `initSentry()` from a top-level client component
 *
 * The dynamic `import()` is intentional — it keeps the ~200KB Sentry
 * bundle out of the initial JS payload when the DSN is unset (the
 * common case in local dev). When you DO wire a DSN, the server-side
 * bundle emits webpack "Critical dependency" warnings because of
 * @sentry/nextjs's OpenTelemetry chain; the warnings are harmless and
 * disappear once you wrap `next.config.mjs` with `withSentryConfig` —
 * deferred to a follow-up PR alongside `instrumentation.ts`.
 */

type SeverityLevel = 'fatal' | 'error' | 'warning' | 'info' | 'debug';

// ── Internal state ────────────────────────────────────────────────────

interface SentryLike {
  captureException: (error: unknown, context?: Record<string, unknown>) => string;
  captureMessage: (message: string, level?: SeverityLevel) => string;
  setUser: (user: { id: string; email?: string } | null) => void;
  setTag: (key: string, value: string) => void;
  addBreadcrumb: (breadcrumb: Record<string, unknown>) => void;
}

let _sentry: SentryLike | null = null;
let _initInFlight: Promise<void> | null = null;

/**
 * Initialize Sentry. Call once from a top-level client component.
 *
 * Safe to call multiple times — concurrent callers share the same
 * in-flight promise instead of racing two SDK boots. Returns
 * immediately on the server (no `window`) and when the DSN is unset.
 */
export async function initSentry(): Promise<void> {
  if (_sentry) return;
  if (typeof window === 'undefined') return;
  const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
  if (!dsn) return;

  if (_initInFlight) return _initInFlight;
  _initInFlight = (async () => {
    const mod = await import('@sentry/nextjs');
    mod.init({
      dsn,
      tracesSampleRate: 0, // errors-only by default; traces are opt-in
      environment: process.env.NEXT_PUBLIC_APP_ENV || 'development',
    });
    _sentry = mod as unknown as SentryLike;
  })();
  return _initInFlight;
}

// ── Public API (all synchronous, safe no-ops) ─────────────────────────

/**
 * Capture an exception and send to Sentry.
 * No-op when Sentry is not configured.
 */
export function captureException(
  error: unknown,
  context?: Record<string, unknown>,
): void {
  _sentry?.captureException(error, context);
}

/**
 * Capture a message at a specific severity level.
 */
export function captureMessage(
  message: string,
  level: SeverityLevel = 'info',
): void {
  _sentry?.captureMessage(message, level);
}

/**
 * Set the current user context for Sentry events.
 */
export function setUser(user: { id: string; email?: string } | null): void {
  _sentry?.setUser(user);
}

/**
 * Set a tag on all future Sentry events.
 */
export function setTag(key: string, value: string): void {
  _sentry?.setTag(key, value);
}

/**
 * Add a navigation breadcrumb.
 */
export function addBreadcrumb(
  category: string,
  message: string,
  level: SeverityLevel = 'info',
): void {
  _sentry?.addBreadcrumb({ category, message, level });
}
