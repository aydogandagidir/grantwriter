import { ArrowRight, Calendar, FileCheck, Sparkles } from 'lucide-react';
import { getTranslations, setRequestLocale } from 'next-intl/server';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { StatusBadge } from '@/components/ui/status-badge';
import { Link } from '@/i18n/navigation';
import { apiServer } from '@/lib/api/server';
import { PROGRAMS, toneClass } from '@/lib/programs';
import { cn } from '@/lib/utils';
import type { MeResponse } from '@bluedev/shared-types';

/**
 * Dashboard — the workspace's home page.
 *
 * Visual rhythm (top → bottom):
 *
 *   1. Period chips     ("AKTIF ÇALIŞMA · 2026") + plan tag
 *   2. Hero band        H1 tenant name + subtitle + primary CTA
 *   3. KPI grid (4)     proposals · drafts · citations · plan
 *   4. Modules section  five program cards w/ status badges
 *
 * Mirrors the Bluedev Tax-co dashboard's spacing + density — caps
 * eyebrows, generous gutters, soft shadows. KPI numbers are loaded
 * server-side via ``apiServer`` so the first paint already has them;
 * if any single call fails the page still renders with a zero so the
 * dashboard never blanks a user out.
 */
export default async function DashboardPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);

  const t = await getTranslations('dashboard');
  const tNav = await getTranslations('nav');
  const tStatus = await getTranslations('nav.status');
  const tPrograms = await getTranslations('programs');

  // ``MeResponse`` is guaranteed by the parent layout's guard, so this
  // call is essentially free (the same row is in TanStack Query cache
  // for the topbar). Loading the count(s) in parallel keeps TTFB tight.
  const [me, kpis] = await Promise.all([
    apiServer<MeResponse>('/api/v1/me'),
    loadKpis(),
  ]);

  const now = new Date();
  const periodLabel = new Intl.DateTimeFormat(locale === 'tr' ? 'tr-TR' : 'en-US', {
    month: 'long',
    year: 'numeric',
  }).format(now);

  return (
    <div className="mx-auto max-w-7xl space-y-8">
      {/* Period + plan chips */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center gap-1.5 rounded-md border border-primary/20 bg-primary/5 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wider text-primary">
          <Calendar className="h-3 w-3" />
          {t('activePeriod')} · {periodLabel}
        </span>
        <span className="rounded-md border bg-muted/40 px-2.5 py-1 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
          {t('planTag')} · {me.plan}
        </span>
      </div>

      {/* Hero band */}
      <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            {t('greeting')}
          </p>
          <h1 className="mt-1 text-3xl font-bold tracking-tight md:text-4xl">
            {me.tenant_name}
          </h1>
          <p className="mt-2 max-w-2xl text-muted-foreground">
            {t('heroSubtitle', { period: periodLabel })}
          </p>
        </div>
        <Link
          href="/proposals/new"
          className="inline-flex items-center gap-2 self-start rounded-md bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground shadow-sm shadow-primary/20 transition-colors hover:bg-primary/90 md:self-end"
        >
          {t('primaryCta')}
          <ArrowRight className="h-4 w-4" />
        </Link>
      </div>

      {/* KPI grid */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <KpiCard
          label={t('kpiActiveProposals')}
          value={kpis.activeProposals}
          hint={t('kpiActiveProposalsHint')}
        />
        <KpiCard
          label={t('kpiDraftsThisMonth')}
          value={kpis.draftsThisMonth}
          hint={t('kpiDraftsThisMonthHint', { period: periodLabel })}
        />
        <KpiCard
          label={t('kpiCitationsVerified')}
          value={kpis.citationsVerified}
          hint={t('kpiCitationsVerifiedHint')}
        />
        <KpiCard
          label={t('kpiPlan')}
          value={me.plan}
          hint={tNav(`roles.${me.role}`)}
          isText
        />
      </div>

      {/* Modules / programs */}
      <section className="space-y-3">
        <header className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            {t('modulesTitle')}
          </h2>
          <p className="text-sm text-muted-foreground">{t('modulesSubtitle')}</p>
        </header>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {PROGRAMS.map((program) => {
            const Icon = program.icon;
            const isSoon = program.status === 'soon';
            const card = (
              <Card
                className={cn(
                  'group h-full transition-colors',
                  isSoon
                    ? 'border-dashed bg-muted/20 opacity-70'
                    : 'hover:border-primary/40 hover:shadow-sm',
                )}
              >
                <CardHeader className="space-y-3">
                  <div className="flex items-start justify-between">
                    <div
                      className={cn(
                        'flex h-9 w-9 items-center justify-center rounded-md',
                        toneClass(program.tone),
                      )}
                    >
                      <Icon className="h-4 w-4" />
                    </div>
                    <StatusBadge variant={program.status}>
                      {tStatus(program.status)}
                    </StatusBadge>
                  </div>
                  <div>
                    <CardTitle className="text-base">
                      {tPrograms(`${program.i18nKey}.name`)}
                    </CardTitle>
                    <CardDescription className="mt-1 line-clamp-2">
                      {tPrograms(`${program.i18nKey}.tagline`)}
                    </CardDescription>
                  </div>
                </CardHeader>
                <CardContent className="border-t pt-3 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                  {tPrograms(`${program.i18nKey}.footer`)}
                </CardContent>
              </Card>
            );

            if (isSoon || !program.href) {
              return (
                <div key={program.id} aria-disabled>
                  {card}
                </div>
              );
            }
            return (
              <Link key={program.id} href={program.href} className="block">
                {card}
              </Link>
            );
          })}
        </div>
      </section>

      {/* Quick suggestions row — keeps the bottom of the screen useful
          before the user has any data of their own. Stays light so the
          page reads as "you have everything you need", not "look at all
          this empty state". */}
      <section className="grid gap-3 md:grid-cols-2">
        <SuggestionCard
          icon={Sparkles}
          title={t('suggestionAiTitle')}
          body={t('suggestionAiBody')}
          ctaLabel={t('suggestionAiCta')}
          ctaHref="/settings/llm-config"
        />
        <SuggestionCard
          icon={FileCheck}
          title={t('suggestionInviteTitle')}
          body={t('suggestionInviteBody')}
          ctaLabel={t('suggestionInviteCta')}
          ctaHref="/settings/invitations"
        />
      </section>
    </div>
  );
}

interface DashboardKpis {
  activeProposals: number;
  draftsThisMonth: number;
  citationsVerified: number;
}

/**
 * Best-effort KPI fetch. Each backend call is tried independently; a
 * single 404 / 500 collapses to ``0`` instead of breaking the whole
 * dashboard. The endpoints are read-only so a stale-by-a-second value
 * is fine.
 */
async function loadKpis(): Promise<DashboardKpis> {
  // Each call is wrapped so one missing endpoint doesn't take the page
  // down — the dashboard's job is to be reassuring, not exhaustive.
  const [proposals, usage] = await Promise.all([
    safeFetch<{ proposals: unknown[] }>('/api/v1/proposals?limit=1000'),
    safeFetch<{ call_count: number }>('/api/v1/tenant/usage'),
  ]);

  return {
    activeProposals: proposals?.proposals?.length ?? 0,
    draftsThisMonth: usage?.call_count ?? 0,
    citationsVerified: 0,
  };
}

async function safeFetch<T>(path: string): Promise<T | null> {
  try {
    return await apiServer<T>(path);
  } catch {
    return null;
  }
}

function KpiCard({
  label,
  value,
  hint,
  isText,
}: {
  label: string;
  value: number | string;
  hint?: string;
  isText?: boolean;
}) {
  const displayValue =
    typeof value === 'number'
      ? new Intl.NumberFormat().format(value)
      : value;
  return (
    <Card>
      <CardContent className="space-y-1.5 p-5">
        <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          {label}
        </p>
        <p
          className={cn(
            'font-bold tracking-tight',
            isText ? 'text-2xl capitalize' : 'text-3xl',
          )}
        >
          {displayValue}
        </p>
        {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      </CardContent>
    </Card>
  );
}

function SuggestionCard({
  icon: Icon,
  title,
  body,
  ctaLabel,
  ctaHref,
}: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  body: string;
  ctaLabel: string;
  ctaHref: string;
}) {
  return (
    <Card className="border-dashed">
      <CardHeader className="flex flex-row items-start gap-3 space-y-0">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
          <Icon className="h-4 w-4" />
        </div>
        <div className="space-y-1">
          <CardTitle className="text-base">{title}</CardTitle>
          <CardDescription>{body}</CardDescription>
          <Link
            href={ctaHref}
            className="inline-flex items-center gap-1 pt-2 text-sm font-medium text-primary hover:underline"
          >
            {ctaLabel}
            <ArrowRight className="h-3.5 w-3.5" />
          </Link>
        </div>
      </CardHeader>
    </Card>
  );
}
