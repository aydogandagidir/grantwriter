import { FileText, PlusCircle } from 'lucide-react';
import { getTranslations, setRequestLocale } from 'next-intl/server';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Link } from '@/i18n/navigation';
import { apiServer } from '@/lib/api/server';
import type { MeResponse } from '@bluedev/shared-types';

export default async function DashboardPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);
  const t = await getTranslations('dashboard');

  // The layout guarantees /me succeeds, but we refetch here so the
  // welcome line gets fresh data (display name edits land instantly).
  const me = await apiServer<MeResponse>('/api/v1/me');

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">{t('title')}</h1>
        <p className="text-muted-foreground">
          {t('welcome', { name: me.display_name ?? me.email ?? '' })}
        </p>
        <p className="mt-1 text-sm text-muted-foreground">
          {t('tenantPlan', { tenant: me.tenant_name, plan: me.plan })}
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>{t('quickActions')}</CardTitle>
          <CardDescription>{me.tenant_name}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-3">
          <Button asChild>
            <Link href="/proposals?new=1">
              <PlusCircle className="h-4 w-4" />
              {t('newProposal')}
            </Link>
          </Button>
          <Button asChild variant="outline">
            <Link href="/proposals">
              <FileText className="h-4 w-4" />
              {t('viewProposals')}
            </Link>
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
