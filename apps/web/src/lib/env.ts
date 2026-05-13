/**
 * Lazy, fail-fast env access. The getters throw the first time a missing
 * required variable is read, NOT at module load — so `next build` can
 * still produce a static skeleton in environments where some envs are
 * intentionally absent (e.g. preview deploys waiting on secret sync).
 *
 * `NEXT_PUBLIC_*` values are exposed to the browser bundle by design.
 * Anything sensitive (service-role keys) MUST NOT be NEXT_PUBLIC_*.
 *
 * IMPORTANT: every getter MUST reference `process.env.NEXT_PUBLIC_FOO` as
 * a literal (no bracket access). Next.js's webpack pipeline statically
 * replaces literal `process.env.X` reads with their build-time values
 * for the browser bundle — but dynamic `process.env[variable]` reads
 * are left untouched and resolve to `undefined` at runtime (browser has
 * no `process` global). Calling `required('FOO')` and then doing
 * `process.env[key]` inside the helper hides the literal from webpack
 * and silently breaks every client-side env consumer.
 */

function required(value: string | undefined, key: string): string {
  if (!value) {
    throw new Error(
      `Missing required environment variable: ${key}. ` +
        'Set it in .env.local or your deploy platform secrets.',
    );
  }
  return value;
}

export const env = {
  get supabaseUrl(): string {
    return required(process.env.NEXT_PUBLIC_SUPABASE_URL, 'NEXT_PUBLIC_SUPABASE_URL');
  },
  get supabaseAnonKey(): string {
    return required(process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY, 'NEXT_PUBLIC_SUPABASE_ANON_KEY');
  },
  get apiUrl(): string {
    return required(process.env.NEXT_PUBLIC_API_URL, 'NEXT_PUBLIC_API_URL');
  },
  // ── Optional: Observability & Analytics ────────────────────────────
  get sentryDsn(): string | undefined {
    return process.env.NEXT_PUBLIC_SENTRY_DSN;
  },
  get posthogKey(): string | undefined {
    return process.env.NEXT_PUBLIC_POSTHOG_KEY;
  },
  get posthogHost(): string | undefined {
    return process.env.NEXT_PUBLIC_POSTHOG_HOST;
  },
  get appEnv(): string {
    return process.env.NEXT_PUBLIC_APP_ENV || 'development';
  },
} as const;