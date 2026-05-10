import { setRequestLocale } from 'next-intl/server';

import { InvitationsPanel } from '@/app/[locale]/(app)/settings/invitations/invitations-panel';

export default async function InvitationsSettingsPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  return <InvitationsPanel />;
}
