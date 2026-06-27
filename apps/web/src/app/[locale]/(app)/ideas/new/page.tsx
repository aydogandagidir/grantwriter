import { setRequestLocale } from 'next-intl/server';

import { IdeaForm } from '@/app/[locale]/(app)/ideas/new/idea-form';

export default async function NewIdeaPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  return <IdeaForm />;
}
