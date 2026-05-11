import { setRequestLocale } from 'next-intl/server';
import { redirect } from 'next/navigation';
import type { ReactNode } from 'react';

import { Sidebar } from '@/components/app-shell/sidebar';
import { Topbar } from '@/components/app-shell/topbar';
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
  } catch {
    // No tenant row yet — send the user to onboarding. The onboarding
    // page itself can decide whether to provision a tenant or surface
    // a "contact your admin" message.
    redirect('/onboarding');
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
