'use client';

import { Building2, Calendar, Euro, Sparkles, Users } from 'lucide-react';
import { useFormatter, useTranslations } from 'next-intl';

import type { CallSummary } from '@bluedev/shared-types';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Link } from '@/i18n/navigation';

/**
 * One card in the /calls grid.
 *
 * Surface area picked to be most useful pre-click: deadline countdown
 * (red <14d, amber <30d, neutral >30d), budget range, TRL band, and
 * consortium-required pill. Title is the clickable affordance — the
 * "View details" button is the secondary entry so keyboard users can
 * tab to it without scrolling the whole card.
 */
export function CallCard({ call }: { call: CallSummary }) {
  const t = useTranslations('calls.card');
  const tStatus = useTranslations('calls.status');
  const format = useFormatter();

  const deadlineLabel =
    call.deadline === null ? t('noDeadline') : format.dateTime(new Date(call.deadline), 'short');

  const daysToDeadline =
    call.deadline === null
      ? null
      : Math.ceil((new Date(call.deadline).getTime() - Date.now()) / (1000 * 60 * 60 * 24));

  const deadlineTone =
    daysToDeadline === null
      ? 'text-muted-foreground'
      : daysToDeadline < 14
        ? 'text-destructive'
        : daysToDeadline < 30
          ? 'text-amber-600 dark:text-amber-400'
          : 'text-muted-foreground';

  return (
    <Card className="flex h-full flex-col" data-testid="call-card" data-call-id={call.id}>
      <CardHeader className="space-y-2">
        <div className="flex items-start justify-between gap-2">
          <Link
            href={`/calls/${call.id}`}
            className="line-clamp-2 text-base font-semibold leading-snug hover:underline"
          >
            {call.title}
          </Link>
          <StatusBadge status={call.status} label={tStatus(call.status)} />
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <span className="flex items-center gap-1">
            <Building2 className="h-3 w-3" />
            {call.programme_id}
            {call.agency_id ? ` / ${call.agency_id}` : null}
          </span>
        </div>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col justify-between gap-3 text-sm">
        <dl className="space-y-1.5">
          <Row
            icon={<Calendar className="h-3.5 w-3.5" />}
            label={t('deadline')}
            value={<span className={deadlineTone}>{deadlineLabel}</span>}
          />
          <Row
            icon={<Euro className="h-3.5 w-3.5" />}
            label={t('budget')}
            value={formatBudget(call)}
          />
          <Row
            icon={<Sparkles className="h-3.5 w-3.5" />}
            label={t('trl')}
            value={formatTrl(call)}
          />
          {call.funding_rate_pct !== null ? (
            <Row
              icon={<Euro className="h-3.5 w-3.5" />}
              label=""
              value={t('fundingRate', { rate: call.funding_rate_pct })}
            />
          ) : null}
        </dl>
        <div className="flex flex-wrap gap-1.5">
          {call.partner_consortium_required ? (
            <Badge variant="outline" className="gap-1 text-xs">
              <Users className="h-3 w-3" />
              {t('consortium')}
            </Badge>
          ) : null}
          {call.eligibility_tags.includes('individual') ? (
            <Badge variant="outline" className="text-xs">
              {t('individual')}
            </Badge>
          ) : null}
          {call.topic_keywords.slice(0, 3).map((kw) => (
            <Badge key={kw} variant="secondary" className="text-xs font-normal">
              {kw}
            </Badge>
          ))}
        </div>
        <Button asChild variant="outline" size="sm" className="self-start">
          <Link href={`/calls/${call.id}`}>{t('viewDetail')}</Link>
        </Button>
      </CardContent>
    </Card>
  );
}

function Row({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="text-muted-foreground">{icon}</span>
      {label ? <dt className="text-muted-foreground">{label}:</dt> : null}
      <dd className="font-medium">{value}</dd>
    </div>
  );
}

function StatusBadge({ status, label }: { status: CallSummary['status']; label: string }) {
  if (status === 'closing_soon') {
    return (
      <Badge variant="outline" className="border-amber-500 text-amber-600 dark:text-amber-400">
        {label}
      </Badge>
    );
  }
  if (status === 'closed') return <Badge variant="outline">{label}</Badge>;
  if (status === 'draft') return <Badge variant="secondary">{label}</Badge>;
  return <Badge>{label}</Badge>;
}

function formatBudget(call: CallSummary): string {
  const lo = call.budget_per_project_min_eur;
  const hi = call.budget_per_project_max_eur;
  if (lo === null && hi === null) return '—';
  const fmt = (n: number) =>
    n >= 1_000_000
      ? `€${(n / 1_000_000).toFixed(n % 1_000_000 === 0 ? 0 : 1)}M`
      : n >= 1_000
        ? `€${(n / 1_000).toFixed(0)}k`
        : `€${n}`;
  if (lo !== null && hi !== null && lo !== hi) return `${fmt(lo)} – ${fmt(hi)}`;
  return fmt((hi ?? lo) as number);
}

function formatTrl(call: CallSummary): string {
  if (call.trl_min === null && call.trl_max === null) return '—';
  if (call.trl_min !== null && call.trl_max !== null && call.trl_min !== call.trl_max)
    return `${call.trl_min}–${call.trl_max}`;
  return String(call.trl_max ?? call.trl_min);
}
