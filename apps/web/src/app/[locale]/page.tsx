import { setRequestLocale } from 'next-intl/server';
import { redirect } from 'next/navigation';

import { createClient } from '@/lib/supabase/server';

/**
 * Root landing page. Redirects to:
 * - /dashboard if the visitor has a valid session
 * - /home (marketing homepage) if not logged in
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

  redirect(user ? '/dashboard' : '/home');
}
