import { setRequestLocale } from 'next-intl/server';

import { ProposalsList } from '@/app/[locale]/(app)/proposals/proposals-list';

export default async function ProposalsListPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);

  return <ProposalsList />;
}
