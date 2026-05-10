import { createServerClient, type CookieOptions } from '@supabase/ssr';
import { type NextRequest, NextResponse } from 'next/server';

import { env } from '@/lib/env';

type CookieToSet = { name: string; value: string; options?: CookieOptions };

/**
 * Middleware-only Supabase client that refreshes the auth cookie on
 * every request. Without this, the access token would expire and the
 * server-side routes would start 401-ing without warning.
 *
 * Returns the (possibly new) response so middleware can short-circuit
 * unauthenticated requests to /(app)/* by inspecting `user`.
 */
export async function updateSession(request: NextRequest) {
  let response = NextResponse.next({
    request,
  });

  const supabase = createServerClient(env.supabaseUrl, env.supabaseAnonKey, {
    cookies: {
      getAll() {
        return request.cookies.getAll();
      },
      setAll(cookiesToSet: CookieToSet[]) {
        for (const { name, value } of cookiesToSet) {
          request.cookies.set(name, value);
        }
        response = NextResponse.next({
          request,
        });
        for (const { name, value, options } of cookiesToSet) {
          response.cookies.set(name, value, options);
        }
      },
    },
  });

  const {
    data: { user },
  } = await supabase.auth.getUser();

  return { response, user };
}
