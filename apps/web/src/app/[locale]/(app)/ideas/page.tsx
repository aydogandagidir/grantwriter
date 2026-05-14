import { setRequestLocale } from 'next-intl/server';

import { IdeasList } from '@/app/[locale]/(app)/ideas/ideas-list';

export default async function IdeasPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  return <IdeasList />;
}
