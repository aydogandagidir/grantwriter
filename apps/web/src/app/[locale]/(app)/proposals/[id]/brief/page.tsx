import { setRequestLocale } from 'next-intl/server';

import { BriefForm } from '@/app/[locale]/(app)/proposals/[id]/brief/brief-form';

export default async function ProposalBriefPage({
  params,
}: {
  params: Promise<{ locale: string; id: string }>;
}) {
  const { locale, id } = await params;
  setRequestLocale(locale);

  return <BriefForm proposalId={id} />;
}
