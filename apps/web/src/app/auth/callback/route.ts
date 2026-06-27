import { NextResponse } from 'next/server';

import { createClient } from '@/lib/supabase/server';

/**
 * Supabase auth callback — the landing point for email-confirmation
 * links, magic links, and OAuth redirects.
 *
 * Lives OUTSIDE the `[locale]` segment so next-intl never rewrites it
 * with a `/tr` or `/en` prefix; the middleware short-circuits
 * `/auth/*` before locale routing runs.
 *
 * Two arrival shapes:
 *
 *   1. Success (PKCE flow): `?code=<auth_code>` — we exchange it for a
 *      session cookie via `exchangeCodeForSession`, then redirect to
 *      `next` (defaults to /onboarding so a fresh signup lands on the
 *      tenant-creation step).
 *
 *   2. Failure: Supabase appends `?error=access_denied&error_code=
 *      otp_expired&error_description=...` when the link is expired or
 *      already consumed. We bounce to /login with a readable flag so the
 *      form can tell the user to request a fresh link, instead of dumping
 *      them on a blank page with the raw error in the URL.
 */
export async function GET(request: Request): Promise<NextResponse> {
  const { searchParams, origin } = new URL(request.url);
  const code = searchParams.get('code');
  const next = searchParams.get('next') ?? '/onboarding';
  const authError = searchParams.get('error');
  const errorCode = searchParams.get('error_code');

  // Expired / invalid / already-used link.
  if (authError) {
    const dest = new URL('/login', origin);
    dest.searchParams.set('auth_error', errorCode ?? authError);
    return NextResponse.redirect(dest);
  }

  if (code) {
    const supabase = await createClient();
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (!error) {
      // `next` is a server-controlled relative path; build it against the
      // request origin so we never honour an attacker-supplied absolute URL.
      const safeNext = next.startsWith('/') ? next : '/onboarding';
      return NextResponse.redirect(new URL(safeNext, origin));
    }
    const dest = new URL('/login', origin);
    dest.searchParams.set('auth_error', 'exchange_failed');
    return NextResponse.redirect(dest);
  }

  // Neither a code nor an error — malformed callback hit directly.
  const dest = new URL('/login', origin);
  dest.searchParams.set('auth_error', 'missing_code');
  return NextResponse.redirect(dest);
}
