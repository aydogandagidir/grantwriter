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

// ── Internal state ────────────────────────────────────────────────────

interface PostHogLike {
  capture: (event: string, properties?: Record<string, unknown>) => void;
  identify: (userId: string, traits?: Record<string, unknown>) => void;
  reset: () => void;
  opt_in_capturing: () => void;
  opt_out_capturing: () => void;
}

let _posthog: PostHogLike | null = null;
let _initInFlight: Promise<void> | null = null;

/**
 * Initialize PostHog. Call once from a top-level client component.
 *
 * Safe to call multiple times — concurrent callers share the in-flight
 * promise. Returns immediately on the server (no `window`) and when
 * `NEXT_PUBLIC_POSTHOG_KEY` is unset.
 */
export async function initAnalytics(): Promise<void> {
  if (_posthog) return;
  if (typeof window === 'undefined') return;

  const key = process.env.NEXT_PUBLIC_POSTHOG_KEY;
  if (!key) return;

  if (_initInFlight) return _initInFlight;
  _initInFlight = (async () => {
    const mod = await import('posthog-js');
    mod.default.init(key, {
      api_host: process.env.NEXT_PUBLIC_POSTHOG_HOST || 'https://eu.posthog.com',
      // Errors-only by default; pageviews are emitted manually via
      // capturePageView() so locale-prefixed routes get clean URLs.
      capture_pageview: false,
      capture_pageleave: true,
      persistence: 'localStorage+cookie',
    });
    _posthog = mod.default as unknown as PostHogLike;
  })();
  return _initInFlight;
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
