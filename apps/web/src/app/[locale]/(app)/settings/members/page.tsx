import { setRequestLocale } from 'next-intl/server';

import { MembersTable } from '@/app/[locale]/(app)/settings/members/members-table';

export default async function MembersPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  return <MembersTable />;
}
