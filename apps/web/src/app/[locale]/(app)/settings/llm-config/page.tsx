import { setRequestLocale } from 'next-intl/server';

import { LlmConfigCard } from '@/app/[locale]/(app)/settings/llm-config/llm-config-card';

export default async function LlmConfigPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  return <LlmConfigCard />;
}
