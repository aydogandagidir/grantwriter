import { ArrowRight } from 'lucide-react';
import { getTranslations, setRequestLocale } from 'next-intl/server';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { StatusBadge } from '@/components/ui/status-badge';
import { Link } from '@/i18n/navigation';
import { apiServer } from '@/lib/api/server';
import { PROGRAMS, toneClass } from '@/lib/programs';
import { cn } from '@/lib/utils';
import type { MeResponse } from '@bluedev/shared-types';

/**
 * Dashboard — workspace home.
 *
 * Linear / Vercel / Stripe dashboard aesthetic: square corners, single
 * hairline borders, no shadows, no decorative chips. The page earns its
 * vertical space with content (hero + KPI + programs) rather than
 * status pills that duplicate what the sidebar already shows.
 *
 *   ┌────────────────────────────────────────────┐
 *   │ Hoş geldiniz                               │  eyebrow
 *   │ bluedev test                  [+ Yeni]     │  H1 + CTA
 *   │ Başvurularını planla, AI ile taslak üret…  │  subtitle
 *   │                                            │
 *   │ ┌──────┐ ┌──────┐ ┌──────┐                 │  3 KPI
 *   │ │AKTIF │ │BU AY │ │ATIF  │                 │
 *   │ │  3   │ │  0   │ │  0   │                 │
 *   │ └──────┘ └──────┘ └──────┘                 │
 *   │                                            │
 *   │ PROGRAMLAR                                 │
 *   │ ┌────┐ ┌────┐ ┌────┐ ┌────┐ ┌────┐         │  5 program cards
 *   └────────────────────────────────────────────┘
 *
 * Plan / period info intentionally lives only in the sidebar — the old
 * design surfaced it three times (sidebar, topbar pills, dashboard
 * chips) for zero added value.
 */
export default async function DashboardPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  setRequestLocale(locale);

  const t = await getTranslations('dashboard');
  const tStatus = await getTranslations('nav.status');
  const tPrograms = await getTranslations('programs');

  const [me, kpis] = await Promise.all([
    apiServer<MeResponse>('/api/v1/me'),
    loadKpis(),
  ]);

  return (
    <div className="mx-auto max-w-6xl space-y-8">
      {/* Hero */}
      <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div className="min-w-0">
          <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            {t('greeting')}
          </p>
          <h1 className="mt-1 truncate text-2xl font-semibold tracking-tight md:text-3xl">
            {me.tenant_name}
          </h1>
          <p className="mt-1.5 max-w-2xl text-sm text-muted-foreground">
            {t('heroSubtitle')}
          </p>
        </div>
        <Link
          href="/proposals/new"
          className="inline-flex h-9 shrink-0 items-center gap-1.5 self-start bg-primary px-3.5 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 md:self-end"
        >
          {t('primaryCta')}
          <ArrowRight className="h-3.5 w-3.5" />
        </Link>
      </div>

      {/* KPI strip — 3 meaningful metrics, no Plan (sidebar already shows it). */}
      <div className="grid gap-px overflow-hidden border bg-border sm:grid-cols-3">
        <KpiCell
          label={t('kpiActiveProposals')}
          value={kpis.activeProposals}
          hint={t('kpiActiveProposalsHint')}
        />
        <KpiCell
          label={t('kpiDraftsThisMonth')}
          value={kpis.draftsThisMonth}
          hint={t('kpiDraftsThisMonthHint')}
        />
        <KpiCell
          label={t('kpiCitationsVerified')}
          value={kpis.citationsVerified}
          hint={t('kpiCitationsVerifiedHint')}
        />
      </div>

      {/* Programs */}
      <section className="space-y-3">
        <header className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            {t('modulesTitle')}
          </h2>
          <p className="text-xs text-muted-foreground">{t('modulesSubtitle')}</p>
        </header>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
          {PROGRAMS.map((program) => {
            const Icon = program.icon;
            const isSoon = program.status === 'soon';
            const card = (
              <Card
                className={cn(
                  'flex h-full flex-col transition-colors',
                  isSoon
                    ? 'border-dashed bg-muted/30'
                    : 'hover:border-primary/40',
                )}
              >
                <CardHeader className="space-y-3 p-4">
                  <div className="flex items-start justify-between">
                    <div
                      className={cn(
                        'flex h-8 w-8 items-center justify-center',
                        toneClass(program.tone),
                      )}
                    >
                      <Icon className="h-4 w-4" />
                    </div>
                    {program.status !== 'active' && (
                      <StatusBadge variant={program.status}>
                        {tStatus(program.status)}
                      </StatusBadge>
                    )}
                  </div>
                  <div>
                    <CardTitle className="text-sm font-semibold leading-tight">
                      {tPrograms(`${program.i18nKey}.name`)}
                    </CardTitle>
                    <CardDescription className="mt-1 line-clamp-2 text-xs">
                      {tPrograms(`${program.i18nKey}.tagline`)}
                    </CardDescription>
                  </div>
                </CardHeader>
                <CardContent className="mt-auto border-t p-3 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                  {tPrograms(`${program.i18nKey}.footer`)}
                </CardContent>
              </Card>
            );

            if (isSoon || !program.href) {
              return (
                <div key={program.id} aria-disabled className="opacity-70">
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
    </div>
  );
}

interface DashboardKpis {
  activeProposals: number;
  draftsThisMonth: number;
  citationsVerified: number;
}

async function loadKpis(): Promise<DashboardKpis> {
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

/**
 * One KPI cell rendered as part of a single bordered strip — the gap
 * between cells comes from ``gap-px`` on the parent container, which
 * lets the shared border draw the divider lines without each card
 * needing its own border.
 */
function KpiCell({
  label,
  value,
  hint,
}: {
  label: string;
  value: number | string;
  hint?: string;
}) {
  const displayValue =
    typeof value === 'number'
      ? new Intl.NumberFormat().format(value)
      : value;
  return (
    <div className="space-y-1 bg-card p-5">
      <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </p>
      <p className="text-2xl font-semibold tracking-tight">{displayValue}</p>
      {hint && <p className="text-[11px] text-muted-foreground">{hint}</p>}
    </div>
  );
}
