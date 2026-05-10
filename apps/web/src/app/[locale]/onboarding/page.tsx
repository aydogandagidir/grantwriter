import { setRequestLocale } from 'next-intl/server';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

/**
 * Stub onboarding page reached when the Supabase user exists but has
 * no public.users row yet (post-signup). The real onboarding flow
 * (pick a tenant name, set up billing) is out of Sprint 3 scope; this
 * page just explains the situation.
 */
export default async function OnboardingPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);

  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/40 px-4 py-12">
      <Card className="w-full max-w-lg">
        <CardHeader>
          <CardTitle>Set up your workspace</CardTitle>
          <CardDescription>
            Your account is created but you&apos;re not in a tenant yet. The
            onboarding flow (workspace name + billing) is coming in Sprint 4.
            If you were invited, open the invitation link in your inbox.
          </CardDescription>
        </CardHeader>
        <CardContent />
      </Card>
    </div>
  );
}
