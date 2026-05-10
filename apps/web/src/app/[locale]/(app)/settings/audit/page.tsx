import { setRequestLocale } from 'next-intl/server';

import { AuditTable } from '@/app/[locale]/(app)/settings/audit/audit-table';

export default async function AuditPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  return <AuditTable />;
}
