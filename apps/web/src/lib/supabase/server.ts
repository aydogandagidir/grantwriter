import { createServerClient, type CookieOptions } from '@supabase/ssr';
import { cookies } from 'next/headers';

import { env } from '@/lib/env';

type CookieToSet = { name: string; value: string; options?: CookieOptions };

/**
 * Server-side Supabase client for Route Handlers, Server Components, and
 * Server Actions. Reads + writes the auth cookie via Next's cookies()
 * API so the session survives across requests.
 *
 * The async cookies() call requires this helper to be invoked inside an
 * async server context — the `await` is intentional and required.
 */
export async function createClient() {
  const cookieStore = await cookies();

  return createServerClient(env.supabaseUrl, env.supabaseAnonKey, {
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet: CookieToSet[]) {
        try {
          for (const { name, value, options } of cookiesToSet) {
            cookieStore.set(name, value, options);
          }
        } catch {
          // The `set` method was called from a Server Component — usually
          // safe to ignore because middleware refreshes the session.
        }
      },
    },
  });
}
