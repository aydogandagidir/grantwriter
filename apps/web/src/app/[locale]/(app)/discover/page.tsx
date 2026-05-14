import { Compass, Sparkles } from 'lucide-react';
import { getTranslations, setRequestLocale } from 'next-intl/server';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Link } from '@/i18n/navigation';

export default async function DiscoverPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations('discover');

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-3xl font-bold tracking-tight">{t('title')}</h1>
        <p className="text-muted-foreground">{t('subtitle')}</p>
      </header>

      <div className="grid gap-4 md:grid-cols-2">
        <Card className="flex flex-col">
          <CardHeader>
            <div className="flex h-10 w-10 items-center justify-center rounded-md bg-primary/10 text-primary">
              <Sparkles className="h-5 w-5" />
            </div>
            <CardTitle className="mt-3">{t('ideaFirst.title')}</CardTitle>
            <CardDescription>{t('ideaFirst.description')}</CardDescription>
          </CardHeader>
          <CardContent className="mt-auto">
            <Button asChild className="w-full">
              <Link href="/ideas/new">{t('ideaFirst.cta')}</Link>
            </Button>
          </CardContent>
        </Card>

        <Card className="flex flex-col">
          <CardHeader>
            <div className="flex h-10 w-10 items-center justify-center rounded-md bg-primary/10 text-primary">
              <Compass className="h-5 w-5" />
            </div>
            <CardTitle className="mt-3">{t('callFirst.title')}</CardTitle>
            <CardDescription>{t('callFirst.description')}</CardDescription>
          </CardHeader>
          <CardContent className="mt-auto">
            <Button asChild variant="outline" className="w-full">
              <Link href="/calls">{t('callFirst.cta')}</Link>
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
