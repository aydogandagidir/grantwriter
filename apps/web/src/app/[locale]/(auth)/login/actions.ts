'use server';

import { redirect } from 'next/navigation';

import { createClient } from '@/lib/supabase/server';

export interface LoginResult {
  ok: boolean;
  error?: string;
}

/**
 * Server action for the login form. Signs in via Supabase Auth and
 * redirects to the requested `next` path (or /dashboard) on success.
 *
 * On error, returns a `{ ok: false, error }` payload — the client form
 * surfaces the message via the toaster instead of doing a hard redirect
 * to an error page.
 */
export async function login(formData: FormData): Promise<LoginResult> {
  const email = String(formData.get('email') ?? '').trim();
  const password = String(formData.get('password') ?? '');
  const next = String(formData.get('next') ?? '/dashboard');

  if (!email || !password) {
    return { ok: false, error: 'Email and password are required.' };
  }

  const supabase = await createClient();
  const { error } = await supabase.auth.signInWithPassword({ email, password });
  if (error) {
    return { ok: false, error: error.message };
  }

  redirect(next);
}
