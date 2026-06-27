import { setRequestLocale } from 'next-intl/server';

import { CallsBrowser } from '@/app/[locale]/(app)/calls/calls-browser';

export default async function CallsBrowsePage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);

  return <CallsBrowser />;
}
