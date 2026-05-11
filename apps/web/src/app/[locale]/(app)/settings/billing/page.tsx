import { setRequestLocale } from 'next-intl/server';

import { BillingPanel } from '@/app/[locale]/(app)/settings/billing/billing-panel';

export default async function BillingPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  return <BillingPanel />;
}
