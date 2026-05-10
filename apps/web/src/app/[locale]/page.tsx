import { setRequestLocale } from 'next-intl/server';
import { redirect } from 'next/navigation';

import { createClient } from '@/lib/supabase/server';

/**
 * Root landing page. Redirects to /dashboard if the visitor has a
 * session, otherwise to /login. Keeps the marketing site separate so
 * `/` always means "do something with the app".
 */
export default async function LocaleHome({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);

  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  redirect(user ? '/dashboard' : '/login');
}
