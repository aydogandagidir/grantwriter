/**
 * PostHog product-analytics integration.
 *
 * The PostHog JS SDK is optional. When `posthog-js` is not installed or
 * `NEXT_PUBLIC_POSTHOG_KEY` is not set, every function is a safe no-op.
 *
 * ## Setup
 *
 * 1. `pnpm add posthog-js` in apps/web
 * 2. Set `NEXT_PUBLIC_POSTHOG_KEY` and `NEXT_PUBLIC_POSTHOG_HOST` in .env.local
 * 3. Call `initAnalytics()` in your Providers or layout
 *
 * ## Key Events (Convention)
 *
 * | Event                    | When                          |
 * |--------------------------|-------------------------------|
 * | onboarding_completed     | Workspace created             |
 * | proposal_created         | New proposal form submitted   |
 * | draft_generated          | Saga completed successfully   |
 * | export_requested         | DOCX/XLSX export triggered    |
 * | byok_configured          | LLM keys saved                |
 * | plan_upgraded            | Checkout completed            |
 */

/* eslint-disable @typescript-eslint/no-unused-vars */

// ── Internal state ────────────────────────────────────────────────────

interface PostHogLike {
  capture: (event: string, properties?: Record<string, unknown>) => void;
  identify: (userId: string, traits?: Record<string, unknown>) => void;
  reset: () => void;
  opt_in_capturing: () => void;
  opt_out_capturing: () => void;
}

let _posthog: PostHogLike | null = null;

/**
 * Initialize PostHog. Call once from your Providers or layout.
 * Async dynamic import — no webpack warnings.
 *
 * Safe to call multiple times (idempotent). Only runs in the browser.
 */
export async function initAnalytics(): Promise<void> {
  if (_posthog) return;
  if (typeof window === 'undefined') return;

  const key = process.env.NEXT_PUBLIC_POSTHOG_KEY;
  if (!key) return;

  // DEBUG: dynamic import temporarily disabled while we isolate the
  // env-substitution corruption caused by @sentry/nextjs server-side
  // OpenTelemetry chain. Re-enable once Track B wires posthog-js into a
  // proper Provider with the env key set.
  //
  // const mod = await import('posthog-js');
  // mod.default.init(key, { ... });
  // _posthog = mod.default;
  void key;
}

// ── Public API (all synchronous, safe no-ops) ─────────────────────────

/**
 * Track a product event.
 */
export function capture(
  event: string,
  properties?: Record<string, unknown>,
): void {
  _posthog?.capture(event, properties);
}

/**
 * Identify the current user (post-login / post-onboarding).
 */
export function identify(
  userId: string,
  traits?: Record<string, unknown>,
): void {
  _posthog?.identify(userId, traits);
}

/**
 * Reset the user identity (on logout).
 */
export function reset(): void {
  _posthog?.reset();
}

/**
 * Enable / disable tracking based on cookie consent.
 */
export function setConsent(enabled: boolean): void {
  if (enabled) {
    _posthog?.opt_in_capturing();
  } else {
    _posthog?.opt_out_capturing();
  }
}

/**
 * Track a page view.
 */
export function capturePageView(url: string): void {
  _posthog?.capture('$pageview', { $current_url: url });
}
