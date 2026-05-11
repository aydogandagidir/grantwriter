import { getTranslations, setRequestLocale } from 'next-intl/server';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';

export default async function ProposalsListPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations('nav');

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">{t('proposals')}</h1>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Proposals (placeholder)</CardTitle>
          <CardDescription>
            The proposal list + editor UI lands in a later sprint. Settings
            pages in the sidebar are wired up to the Sprint 3 backend.
          </CardDescription>
        </CardHeader>
        <CardContent />
      </Card>
    </div>
  );
}
