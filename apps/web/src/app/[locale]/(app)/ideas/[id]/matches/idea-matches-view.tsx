'use client';

import { Loader2, Sparkles, Target } from 'lucide-react';
import { useLocale, useTranslations } from 'next-intl';

import type { CallMatchOut } from '@bluedev/shared-types';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Link } from '@/i18n/navigation';
import { useIdeaDetail, useIdeaMatches, useMatchIdea } from '@/lib/api/queries';

export function IdeaMatchesView({ ideaId }: { ideaId: string }) {
  const t = useTranslations('ideas.matches');
  const locale = useLocale();
  const { data: idea } = useIdeaDetail(ideaId);
  const { data: matchData, isLoading } = useIdeaMatches(ideaId);
  const matchIdea = useMatchIdea();

  const matches = matchData?.matches ?? [];
  const hasRun = matches.length > 0 || matchData?.computed_at !== '';

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">
            {idea?.title ?? t('title')}
          </h1>
          <p className="text-muted-foreground">{t('subtitle')}</p>
        </div>
        <Button
          onClick={() => matchIdea.mutate({ ideaId })}
          disabled={matchIdea.isPending}
          data-testid="run-matcher"
        >
          {matchIdea.isPending ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              {t('matching')}
            </>
          ) : (
            <>
              <Sparkles className="h-4 w-4" />
              {hasRun ? t('rerun') : t('runMatch')}
            </>
          )}
        </Button>
      </header>

      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, idx) => (
            <Skeleton key={idx} className="h-44 w-full" />
          ))}
        </div>
      ) : matchIdea.isPending ? (
        <Card>
          <CardContent className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            {t('matching')}
          </CardContent>
        </Card>
      ) : matches.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 py-12 text-center">
            <Target className="h-8 w-8 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              {hasRun ? t('noResults') : t('empty')}
            </p>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4" data-testid="match-list">
          {matches.map((match) => (
            <MatchCard key={match.call_id} match={match} locale={locale} t={t} />
          ))}
        </div>
      )}
    </div>
  );
}

function MatchCard({
  match,
  locale,
  t,
}: {
  match: CallMatchOut;
  locale: string;
  t: ReturnType<typeof useTranslations>;
}) {
  const rationale =
    locale === 'tr' ? match.rationale_tr : match.rationale_en;
  const scorePct = Math.round(match.total_score * 100);

  return (
    <Card data-testid="match-card" data-call-id={match.call_id}>
      <CardHeader className="space-y-2">
        <div className="flex items-start justify-between gap-3">
          <CardTitle className="text-base leading-snug">
            {match.call_title ?? match.call_id}
          </CardTitle>
          <Badge className="shrink-0" data-testid="match-score">
            {t('matchScore', { score: scorePct })}
          </Badge>
        </div>
        {match.programme_id ? (
          <p className="text-xs text-muted-foreground">{match.programme_id}</p>
        ) : null}
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <ScoreBreakdown match={match} t={t} />

        {rationale ? (
          <div>
            <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {t('whyMatch')}
            </p>
            <p className="text-muted-foreground">{rationale}</p>
          </div>
        ) : null}

        {match.identified_gaps.length > 0 ? (
          <div>
            <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              {t('gaps')}
            </p>
            <ul className="list-inside list-disc space-y-0.5 text-muted-foreground">
              {match.identified_gaps.map((gap, idx) => (
                <li key={idx}>{gap}</li>
              ))}
            </ul>
          </div>
        ) : null}

        <Button asChild variant="outline" size="sm">
          <Link href={`/calls/${match.call_id}`}>{t('viewCall')}</Link>
        </Button>
      </CardContent>
    </Card>
  );
}

function ScoreBreakdown({
  match,
  t,
}: {
  match: CallMatchOut;
  t: ReturnType<typeof useTranslations>;
}) {
  const rows: Array<{ label: string; value: number }> = [
    { label: t('semantic'), value: match.semantic_score },
    { label: t('keyword'), value: match.keyword_overlap_score },
    { label: t('sector'), value: match.sector_score },
    { label: t('trlFit'), value: match.trl_fit_score },
    { label: t('budgetFit'), value: match.budget_fit_score },
  ];
  return (
    <div>
      <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {t('scoreBreakdown')}
      </p>
      <div className="space-y-1">
        {rows.map((row) => (
          <div key={row.label} className="flex items-center gap-2">
            <span className="w-20 shrink-0 text-xs text-muted-foreground">
              {row.label}
            </span>
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary"
                style={{ width: `${Math.round(Math.max(0, Math.min(1, row.value)) * 100)}%` }}
              />
            </div>
            <span className="w-9 shrink-0 text-right text-xs tabular-nums text-muted-foreground">
              {Math.round(row.value * 100)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
