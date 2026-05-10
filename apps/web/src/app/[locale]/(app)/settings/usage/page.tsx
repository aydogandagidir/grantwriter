import { setRequestLocale } from 'next-intl/server';

import { UsageReport } from '@/app/[locale]/(app)/settings/usage/usage-report';

export default async function UsagePage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  return <UsageReport />;
}
