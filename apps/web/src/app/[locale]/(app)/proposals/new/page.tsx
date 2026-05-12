import { setRequestLocale } from 'next-intl/server';

import { NewProposalForm } from '@/app/[locale]/(app)/proposals/new/new-proposal-form';

export default async function NewProposalPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);

  return <NewProposalForm locale={locale} />;
}
