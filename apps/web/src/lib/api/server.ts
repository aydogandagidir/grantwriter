import 'server-only';

import { createClient } from '@/lib/supabase/server';

import { type ApiRequestInit, call } from './client';

/**
 * Server-side API call (Route Handler / Server Component / Server Action).
 *
 * Mirrors `apiClient` but pulls the Supabase session from cookies via
 * the server client. Marked `server-only` so any accidental client
 * import fails the build with a clear error instead of silently
 * dragging `next/headers` into the browser bundle.
 */
export async function apiServer<T>(path: string, init: ApiRequestInit = {}): Promise<T> {
  const supabase = await createClient();
  const { data } = await supabase.auth.getSession();
  return call<T>(path, init, data.session?.access_token ?? null);
}
