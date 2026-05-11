'use server';

import { redirect } from 'next/navigation';

import { createClient } from '@/lib/supabase/server';

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
  const { data, error } = await supabase.auth.signUp({ email, password });
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
