'use server';

import { headers } from 'next/headers';
import { redirect } from 'next/navigation';

import { createClient } from '@/lib/supabase/server';

/**
 * Resolve the deployment origin from request headers so the email
 * confirmation link points back to the SAME host the user signed up on
 * (production, a preview deploy, or localhost) — never a hard-coded URL.
 *
 * Falls back to the forwarded host (Vercel/Render set x-forwarded-*)
 * then to NEXT_PUBLIC_APP_URL if a proxy strips Origin.
 */
async function resolveOrigin(): Promise<string | null> {
  const h = await headers();
  const origin = h.get('origin');
  if (origin) return origin;
  const host = h.get('x-forwarded-host') ?? h.get('host');
  if (host) {
    const proto = h.get('x-forwarded-proto') ?? 'https';
    return `${proto}://${host}`;
  }
  return process.env.NEXT_PUBLIC_APP_URL ?? null;
}

export interface SignupResult {
  ok: boolean;
  error?: string;
  needsConfirmation?: boolean;
}

/**
 * Server action for the signup form. Creates the Supabase Auth user;
 * the tenant + public.users row are NOT created here — that flow lands
 * in a follow-up onboarding step (creating-tenant page) so the user
 * picks a tenant name first.
 *
 * If Supabase requires email confirmation, we surface
 * `needsConfirmation` so the form can show a "check your inbox" state.
 */
export async function signup(formData: FormData): Promise<SignupResult> {
  const email = String(formData.get('email') ?? '').trim();
  const password = String(formData.get('password') ?? '');

  if (!email || !password) {
    return { ok: false, error: 'Email and password are required.' };
  }
  if (password.length < 8) {
    return { ok: false, error: 'Password must be at least 8 characters.' };
  }

  const supabase = await createClient();
  const origin = await resolveOrigin();
  const { data, error } = await supabase.auth.signUp({
    email,
    password,
    // Without this, Supabase falls back to its dashboard "Site URL"
    // setting (defaults to http://localhost:3000) — the bug that sent
    // production signups to a dead localhost link. The /auth/callback
    // route exchanges the confirmation code for a session and forwards
    // to onboarding.
    options: origin
      ? { emailRedirectTo: `${origin}/auth/callback?next=/onboarding` }
      : undefined,
  });
  if (error) {
    return { ok: false, error: error.message };
  }

  // Supabase returns user + session when email confirmation is off, or
  // just user when on. The dashboard's middleware will redirect to
  // /onboarding if the user has no tenant yet.
  if (!data.session) {
    return { ok: true, needsConfirmation: true };
  }

  redirect('/dashboard');
}
