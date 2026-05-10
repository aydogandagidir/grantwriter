/**
 * Lazy, fail-fast env access. The getters throw the first time a missing
 * required variable is read, NOT at module load — so `next build` can
 * still produce a static skeleton in environments where some envs are
 * intentionally absent (e.g. preview deploys waiting on secret sync).
 *
 * `NEXT_PUBLIC_*` values are exposed to the browser bundle by design.
 * Anything sensitive (service-role keys) MUST NOT be NEXT_PUBLIC_*.
 */

function required(key: string): string {
  const value = process.env[key];
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
    return required('NEXT_PUBLIC_SUPABASE_URL');
  },
  get supabaseAnonKey(): string {
    return required('NEXT_PUBLIC_SUPABASE_ANON_KEY');
  },
  get apiUrl(): string {
    return required('NEXT_PUBLIC_API_URL');
  },
} as const;
