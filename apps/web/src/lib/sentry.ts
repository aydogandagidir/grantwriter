/**
 * Frontend Sentry error-tracking integration.
 *
 * Mirrors the backend pattern (src/core/observability.py): the SDK is
 * optional — when `@sentry/nextjs` is not installed or the DSN is
 * missing, all functions become safe no-ops.
 *
 * ## Setup
 *
 * 1. `pnpm add @sentry/nextjs` in apps/web
 * 2. Set `NEXT_PUBLIC_SENTRY_DSN` in .env.local
 * 3. Wrap next.config.mjs with `withSentryConfig` (see Sentry docs)
 * 4. Call `initSentry()` in your root layout or providers
 *
 * Until step 1 is done, this module exports no-op stubs.
 */

type SeverityLevel = 'fatal' | 'error' | 'warning' | 'info' | 'debug';

/* eslint-disable @typescript-eslint/no-unused-vars */

// ── Internal state ────────────────────────────────────────────────────

interface SentryLike {
  captureException: (error: unknown, context?: Record<string, unknown>) => string;
  captureMessage: (message: string, level?: SeverityLevel) => string;
  setUser: (user: { id: string; email?: string } | null) => void;
  setTag: (key: string, value: string) => void;
  addBreadcrumb: (breadcrumb: Record<string, unknown>) => void;
}

let _sentry: SentryLike | null = null;

/**
 * Initialize Sentry. Call once from your Providers or layout.
 * Async dynamic import — does NOT trigger webpack module resolution
 * warnings because `import()` with a variable is deferred to runtime.
 *
 * Safe to call multiple times (idempotent).
 */
export async function initSentry(): Promise<void> {
  if (_sentry) return;

  const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
  if (!dsn) return;

  // DEBUG: dynamic import temporarily disabled because @sentry/nextjs'
  // server-side bundle (OpenTelemetry / fastify-otel) triggers webpack
  // "Critical dependency" warnings that appear to corrupt the
  // NEXT_PUBLIC_* env substitution. Re-enable once we wire next.config.mjs
  // with `withSentryConfig` properly (Track B / Sprint 4 Day 16 Aşama D).
  //
  // const mod = await import('@sentry/nextjs');
  // mod.init({ dsn, ... });
  // _sentry = mod;
  void dsn;  // keep the early-return + reference so eslint is happy
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
