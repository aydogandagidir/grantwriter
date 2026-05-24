import { setRequestLocale } from 'next-intl/server';
import { redirect } from 'next/navigation';
import type { ReactNode } from 'react';

import { Sidebar } from '@/components/app-shell/sidebar';
import { Topbar } from '@/components/app-shell/topbar';
import { ApiError } from '@/lib/api/client';
import { apiServer } from '@/lib/api/server';
import { createClient } from '@/lib/supabase/server';
import type { MeResponse } from '@bluedev/shared-types';

/**
 * Authenticated app shell. Fetches /me server-side so we can:
 *
 * 1. Redirect to /onboarding if the user has no tenant yet (Supabase
 *    user exists but no public.users row — happens right after signup).
 * 2. Pass the role into the Sidebar so admin-only sections render only
 *    for owners/admins.
 *
 * Children pages re-fetch the same /me client-side via useMe() — the
 * server fetch warms the TanStack Query cache via initial data hydration
 * only when we add a per-page hydrator. For now the duplicate fetch is
 * cheap (single DB row) and keeps the layout simple.
 */
export default async function AppLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);

  // Middleware already redirected unauthenticated requests, but Supabase
  // user without a tenant (post-signup) needs its own redirect.
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect('/login');

  let me: MeResponse;
  try {
    me = await apiServer<MeResponse>('/api/v1/me');
  } catch (err) {
    // 404 == Supabase user exists but has no public.users row yet (the
    // post-signup state). That — and only that — means "needs onboarding".
    if (err instanceof ApiError && err.status === 404) {
      redirect('/onboarding');
    }
    // Any other failure (transient 5xx while the backend cold-starts, a
    // network blip) must NOT be misread as "no tenant" — doing so would
    // bounce an existing user out of their workspace and into onboarding.
    // Re-throw so the error boundary shows a retry UI instead. NB:
    // redirect() above throws NEXT_REDIRECT (not an ApiError), so it
    // propagates past this re-throw unharmed.
    throw err;
  }

  return (
    <div className="flex min-h-screen">
      <Sidebar role={me.role} />
      <div className="flex flex-1 flex-col">
        <Topbar
          email={me.email}
          displayName={me.display_name}
          tenantName={me.tenant_name}
          role={me.role}
        />
        <main className="flex-1 overflow-y-auto bg-muted/20 p-6">{children}</main>
      </div>
    </div>
  );
}
