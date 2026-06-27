import { setRequestLocale } from 'next-intl/server';

import { CallDetailView } from '@/app/[locale]/(app)/calls/[id]/call-detail-view';

export default async function CallDetailPage({
  params,
}: {
  params: Promise<{ locale: string; id: string }>;
}) {
  const { locale, id } = await params;
  setRequestLocale(locale);

  return <CallDetailView callId={id} />;
}
