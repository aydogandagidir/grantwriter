'use client';

import { ArrowLeft, Calendar, ExternalLink, FileDown, Globe } from 'lucide-react';
import { useFormatter, useTranslations } from 'next-intl';

import type { CallDetail } from '@bluedev/shared-types';

import { CallDiscoveryPanel } from '@/app/[locale]/(app)/calls/[id]/call-discovery-panel';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';
import { Skeleton } from '@/components/ui/skeleton';
import { Link } from '@/i18n/navigation';
import { useCallDetail } from '@/lib/api/queries';

export function CallDetailView({ callId }: { callId: string }) {
  const t = useTranslations('calls.detail');
  const tCard = useTranslations('calls.card');
  const tStatus = useTranslations('calls.status');
  const format = useFormatter();

  const { data, isLoading, isError } = useCallDetail(callId);

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-2/3" />
        <Skeleton className="h-4 w-1/3" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  if (isError || !data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{t('notFoundTitle')}</CardTitle>
          <CardDescription>{t('notFoundDescription')}</CardDescription>
        </CardHeader>
        <CardContent>
          <Button asChild variant="outline">
            <Link href="/calls">
              <ArrowLeft className="h-4 w-4" />
              {t('back')}
            </Link>
          </Button>
        </CardContent>
      </Card>
    );
  }

  const call: CallDetail = data;

  return (
    <div className="space-y-6">
      <div>
        <Button asChild variant="ghost" size="sm" className="-ml-2">
          <Link href="/calls">
            <ArrowLeft className="h-4 w-4" />
            {t('back')}
          </Link>
        </Button>
      </div>

      <header className="space-y-2">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <h1 className="text-2xl font-bold tracking-tight">{call.title}</h1>
            <p className="text-sm text-muted-foreground">
              {call.programme_id}
              {call.agency_id ? ` · ${call.agency_id}` : null}
              {' · '}
              {call.source}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline">{tStatus(call.status)}</Badge>
            {call.funding_rate_pct !== null ? (
              <Badge variant="secondary">
                {tCard('fundingRate', { rate: call.funding_rate_pct })}
              </Badge>
            ) : null}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {call.call_url ? (
            <Button asChild variant="default" size="sm">
              <a href={call.call_url} target="_blank" rel="noreferrer noopener">
                <ExternalLink className="h-4 w-4" />
                {t('applyAt')}
              </a>
            </Button>
          ) : null}
          {call.work_programme_pdf_url ? (
            <Button asChild variant="outline" size="sm">
              <a
                href={call.work_programme_pdf_url}
                target="_blank"
                rel="noreferrer noopener"
              >
                <FileDown className="h-4 w-4" />
                {t('downloadWorkProgramme')}
              </a>
            </Button>
          ) : null}
          {call.application_form_url ? (
            <Button asChild variant="outline" size="sm">
              <a
                href={call.application_form_url}
                target="_blank"
                rel="noreferrer noopener"
              >
                <FileDown className="h-4 w-4" />
                {t('applicationForm')}
              </a>
            </Button>
          ) : null}
        </div>
      </header>

      <CallDiscoveryPanel callId={callId} />

      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t('keyDates')}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <DateRow
              icon={<Calendar className="h-3.5 w-3.5" />}
              label={t('openingAt')}
              value={
                call.opening_at
                  ? format.dateTime(new Date(call.opening_at), 'short')
                  : '—'
              }
            />
            <DateRow
              icon={<Calendar className="h-3.5 w-3.5" />}
              label={t('deadline')}
              value={
                call.deadline
                  ? format.dateTime(new Date(call.deadline), 'short')
                  : tCard('noDeadline')
              }
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">{tCard('budget')}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p className="text-lg font-semibold">{formatBudget(call)}</p>
            {call.trl_min !== null || call.trl_max !== null ? (
              <p className="text-xs text-muted-foreground">
                {tCard('trl')}: {formatTrl(call)}
              </p>
            ) : null}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t('eligibility')}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <div className="flex flex-wrap gap-1.5">
              {call.eligibility_tags.length === 0 ? (
                <span className="text-xs text-muted-foreground">—</span>
              ) : (
                call.eligibility_tags.map((tag) => (
                  <Badge key={tag} variant="secondary" className="text-xs">
                    {tag}
                  </Badge>
                ))
              )}
            </div>
            {call.partner_consortium_required ? (
              <p className="text-xs text-amber-600 dark:text-amber-400">
                {tCard('consortium')}
              </p>
            ) : null}
            {call.geo_scope.length > 0 ? (
              <p className="flex items-center gap-1 text-xs text-muted-foreground">
                <Globe className="h-3 w-3" />
                {call.geo_scope.join(', ')}
              </p>
            ) : null}
          </CardContent>
        </Card>
      </div>

      {call.scope_summary ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t('scopeSummary')}</CardTitle>
          </CardHeader>
          <CardContent className="whitespace-pre-line text-sm leading-relaxed text-muted-foreground">
            {call.scope_summary}
          </CardContent>
        </Card>
      ) : null}

      {call.call_text && call.call_text !== call.scope_summary ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t('callText')}</CardTitle>
          </CardHeader>
          <CardContent className="whitespace-pre-line text-sm leading-relaxed text-muted-foreground">
            {call.call_text.slice(0, 5000)}
            {call.call_text.length > 5000 ? '…' : null}
          </CardContent>
        </Card>
      ) : null}

      {call.topic_keywords.length > 0 ? (
        <>
          <Separator />
          <div className="flex flex-wrap gap-1.5">
            {call.topic_keywords.map((kw) => (
              <Badge key={kw} variant="secondary" className="font-normal">
                {kw}
              </Badge>
            ))}
          </div>
        </>
      ) : null}
    </div>
  );
}

function DateRow({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="text-muted-foreground">{icon}</span>
      <span className="text-muted-foreground">{label}:</span>
      <span className="font-medium">{value}</span>
    </div>
  );
}

function formatBudget(call: CallDetail): string {
  const lo = call.budget_per_project_min_eur;
  const hi = call.budget_per_project_max_eur;
  if (lo === null && hi === null) {
    return call.budget_total_eur !== null
      ? `€${call.budget_total_eur.toLocaleString('en-IE')}`
      : '—';
  }
  const fmt = (n: number) =>
    n >= 1_000_000
      ? `€${(n / 1_000_000).toFixed(n % 1_000_000 === 0 ? 0 : 1)}M`
      : n >= 1_000
        ? `€${(n / 1_000).toFixed(0)}k`
        : `€${n}`;
  if (lo !== null && hi !== null && lo !== hi) return `${fmt(lo)} – ${fmt(hi)}`;
  return fmt((hi ?? lo) as number);
}

function formatTrl(call: CallDetail): string {
  if (call.trl_min === null && call.trl_max === null) return '—';
  if (call.trl_min !== null && call.trl_max !== null && call.trl_min !== call.trl_max)
    return `${call.trl_min}–${call.trl_max}`;
  return String(call.trl_max ?? call.trl_min);
}
