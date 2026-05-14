import { setRequestLocale } from 'next-intl/server';

import { IdeaMatchesView } from '@/app/[locale]/(app)/ideas/[id]/matches/idea-matches-view';

export default async function IdeaMatchesPage({
  params,
}: {
  params: Promise<{ locale: string; id: string }>;
}) {
  const { locale, id } = await params;
  setRequestLocale(locale);
  return <IdeaMatchesView ideaId={id} />;
}
