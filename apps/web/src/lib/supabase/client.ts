'use client';

import { createBrowserClient } from '@supabase/ssr';

import { env } from '@/lib/env';

/**
 * Browser-side Supabase client. Uses the anon key + cookies via the SSR
 * helper, so the same auth session is shared with the server-side
 * clients below — no manual JWT plumbing needed.
 *
 * Returns a fresh instance per call so it doesn't get serialised into
 * the RSC payload by accident.
 */
export function createClient() {
  return createBrowserClient(env.supabaseUrl, env.supabaseAnonKey);
}
