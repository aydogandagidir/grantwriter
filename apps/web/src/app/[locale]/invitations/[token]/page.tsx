import { getTranslations, setRequestLocale } from 'next-intl/server';

import { AcceptInvitationCard } from '@/app/[locale]/invitations/[token]/accept-card';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { env } from '@/lib/env';
import type { InvitationPreview } from '@bluedev/shared-types';

async function fetchPreview(token: string): Promise<InvitationPreview | { error: string }> {
  // Public endpoint — no JWT required. Call the API directly so the page
  // can render server-side without depending on the user's session.
  const url = `${env.apiUrl.replace(/\/$/, '')}/api/v1/invitations/${token}`;
  const response = await fetch(url, { cache: 'no-store' });
  if (!response.ok) {
    if (response.status === 404) return { error: 'notFound' };
    if (response.status === 410) {
      const body = await response.json().catch(() => null);
      const detail = String(body?.detail ?? '');
      if (detail.includes('accepted')) return { error: 'alreadyAccepted' };
      return { error: 'expired' };
    }
    return { error: 'notFound' };
  }
  return (await response.json()) as InvitationPreview;
}

export default async function AcceptInvitationPage({
  params,
}: {
  params: Promise<{ locale: string; token: string }>;
}) {
  const { locale, token } = await params;
  setRequestLocale(locale);
  const t = await getTranslations('invitationAccept');

  const preview = await fetchPreview(token);

  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/40 px-4 py-12">
      <div className="w-full max-w-lg">
        <Card>
          <CardHeader>
            <CardTitle>{t('title')}</CardTitle>
            {'error' in preview ? (
              <CardDescription className="text-destructive">{t(preview.error)}</CardDescription>
            ) : (
              <CardDescription>
                {t('invited', { tenant: preview.tenant_name, role: preview.role })}
              </CardDescription>
            )}
          </CardHeader>
          <CardContent>
            {'error' in preview ? null : (
              <AcceptInvitationCard token={token} preview={preview} />
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
