'use client';

import { CheckCircle2, Loader2, ShieldCheck, Sparkles, XCircle } from 'lucide-react';
import { useLocale, useTranslations } from 'next-intl';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  type EligibilityReport,
  type GenerateIdeasResponse,
  useCallEligibility,
  useGenerateIdeasForCall,
} from '@/lib/api/queries';

/**
 * The 'Browse calls → generate ideas' + pre-brief eligibility surface,
 * mounted on the call detail page. Both actions are user-triggered
 * (not auto-run on mount) so we don't burn an LLM call or a DB round-
 * trip for every call view.
 */
export function CallDiscoveryPanel({ callId }: { callId: string }) {
  const t = useTranslations('calls.discovery');
  const locale = useLocale();

  const generate = useGenerateIdeasForCall();
  // enabled:false → the query only runs when the user clicks (refetch).
  const eligibility = useCallEligibility(callId, { enabled: false });

  const ideas: GenerateIdeasResponse | undefined = generate.data;

  return (
    <div className="grid gap-4 md:grid-cols-2">
      {/* ── Generate ideas ─────────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Sparkles className="h-4 w-4 text-primary" />
            {t('generatedTitle')}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {!ideas && !generate.isPending ? (
            <Button
              onClick={() => generate.mutate({ callId })}
              data-testid="generate-ideas"
              className="w-full"
            >
              <Sparkles className="h-4 w-4" />
              {t('generateCta')}
            </Button>
          ) : null}

          {generate.isPending ? (
            <p className="flex items-center justify-center gap-2 py-6 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              {t('generating')}
            </p>
          ) : null}

          {ideas ? (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                {ideas.from_cache ? (
                  <Badge variant="secondary" className="text-xs">
                    {t('fromCache')}
                  </Badge>
                ) : (
                  <span />
                )}
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    generate.mutate({ callId, forceRefresh: true })
                  }
                  disabled={generate.isPending}
                >
                  {t('regenerate')}
                </Button>
              </div>
              {ideas.ideas.map((idea, idx) => (
                <div
                  key={idx}
                  className="rounded-md border p-3"
                  data-testid="generated-idea"
                >
                  <div className="flex items-start justify-between gap-2">
                    <p className="text-sm font-semibold leading-snug">
                      {idea.title}
                    </p>
                    <Badge className="shrink-0 text-xs">
                      {t('alignment', {
                        score: Math.round(idea.alignment_score * 100),
                      })}
                    </Badge>
                  </div>
                  <p className="mt-1 line-clamp-3 text-xs text-muted-foreground">
                    {idea.abstract}
                  </p>
                  {idea.suggested_consortium_type ? (
                    <p className="mt-1.5 text-xs text-muted-foreground">
                      <span className="font-medium">{t('consortium')}:</span>{' '}
                      {idea.suggested_consortium_type}
                    </p>
                  ) : null}
                </div>
              ))}
            </div>
          ) : null}
        </CardContent>
      </Card>

      {/* ── Eligibility ────────────────────────────────────────────── */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <ShieldCheck className="h-4 w-4 text-primary" />
            {t('eligibilityTitle')}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {!eligibility.data && !eligibility.isFetching ? (
            <Button
              variant="outline"
              onClick={() => eligibility.refetch()}
              data-testid="check-eligibility"
              className="w-full"
            >
              <ShieldCheck className="h-4 w-4" />
              {t('eligibilityCta')}
            </Button>
          ) : null}

          {eligibility.isFetching ? (
            <p className="flex items-center justify-center gap-2 py-6 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              {t('checking')}
            </p>
          ) : null}

          {eligibility.data ? (
            <EligibilityResult report={eligibility.data} locale={locale} t={t} />
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}

function EligibilityResult({
  report,
  locale,
  t,
}: {
  report: EligibilityReport;
  locale: string;
  t: ReturnType<typeof useTranslations>;
}) {
  return (
    <div className="space-y-3" data-testid="eligibility-result">
      <div className="flex items-center justify-between">
        <VerdictBadge verdict={report.verdict} label={t(`verdict.${report.verdict}`)} />
        <span className="text-xs text-muted-foreground">
          {t('confidence', { score: Math.round(report.confidence * 100) })}
        </span>
      </div>
      <ul className="space-y-1.5">
        {report.checks.map((check) => (
          <li key={check.rule} className="flex items-start gap-2 text-xs">
            <CheckIcon status={check.status} />
            <span className="text-muted-foreground">
              {locale === 'tr' ? check.message_tr : check.message_en}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function VerdictBadge({
  verdict,
  label,
}: {
  verdict: EligibilityReport['verdict'];
  label: string;
}) {
  if (verdict === 'ELIGIBLE') return <Badge>{label}</Badge>;
  if (verdict === 'CONDITIONAL') {
    return (
      <Badge variant="outline" className="border-amber-500 text-amber-600 dark:text-amber-400">
        {label}
      </Badge>
    );
  }
  return <Badge variant="destructive">{label}</Badge>;
}

function CheckIcon({ status }: { status: 'pass' | 'warn' | 'fail' }) {
  if (status === 'pass') {
    return <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-600 dark:text-emerald-400" />;
  }
  if (status === 'fail') {
    return <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-destructive" />;
  }
  return (
    <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-400" />
  );
}
