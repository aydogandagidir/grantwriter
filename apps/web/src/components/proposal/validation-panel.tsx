'use client';

import { AlertTriangle, CheckCircle2, FileDown, Loader2, ShieldAlert } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useState } from 'react';

import type { HuntReport, ValidationIssue, ValidationReport } from '@bluedev/shared-types';

import {
  useCachedValidation,
  useValidateProposal,
} from '@/lib/api/queries';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';

/**
 * Compliance + Hallucination Hunter surface (S3.D13.T1 completion).
 *
 * Renders three things, top-to-bottom:
 *
 * 1. Status badge — green "ready to export" or red "export blocked".
 *    Derived from `compliance.passed AND hunt.recommendation === "ok"`.
 * 2. Compliance issues list — every blocker/warning/info from the
 *    formal-rule + LLM depth checks. Hunt blockers also appear here
 *    (code="hunt_blocked").
 * 3. Citation verification panel — verified/fabricated counts, claim
 *    support rate, and the flagged citations list when the hunt is
 *    unhappy.
 *
 * The export button (a separate component below) disables when either
 * gate trips — single source of truth: `isExportBlocked(report)`.
 */
export function ValidationPanel({ proposalId }: { proposalId: string }) {
  const t = useTranslations('validation');
  const cached = useCachedValidation(proposalId);
  const validate = useValidateProposal(proposalId);

  const report = cached ?? validate.data;
  const blocked = report ? isExportBlocked(report) : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('title')}</CardTitle>
        <CardDescription>{t('subtitle')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <StatusBadge blocked={blocked} />
          <Button
            variant="outline"
            size="sm"
            onClick={() => validate.mutate()}
            disabled={validate.isPending}
          >
            {validate.isPending ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
                {t('revalidating')}
              </>
            ) : (
              t('revalidate')
            )}
          </Button>
        </div>
        {report ? (
          <>
            <IssuesList issues={report.compliance.issues} />
            <HuntSection report={report.hallucination_hunter} />
            <ExportButton proposalId={proposalId} report={report} />
          </>
        ) : (
          <p className="text-sm text-muted-foreground">{t('never')}</p>
        )}
      </CardContent>
    </Card>
  );
}

/**
 * Pure export-gate helper. Exported so the proposal page can wire a
 * top-of-page warning banner without re-deriving the rule.
 */
export function isExportBlocked(report: ValidationReport): boolean {
  if (!report.compliance.passed) {
    return true;
  }
  if (report.hallucination_hunter?.recommendation === 'block_export') {
    return true;
  }
  return false;
}

function StatusBadge({ blocked }: { blocked: boolean | null }) {
  const t = useTranslations('validation');
  if (blocked === null) {
    return null;
  }
  if (blocked) {
    return (
      <Badge variant="destructive" className="gap-2" data-testid="validation-status">
        <ShieldAlert className="h-4 w-4" aria-hidden="true" />
        {t('failed')}
      </Badge>
    );
  }
  return (
    <Badge
      variant="secondary"
      className="gap-2 bg-emerald-50 text-emerald-700 hover:bg-emerald-50 dark:bg-emerald-950 dark:text-emerald-300"
      data-testid="validation-status"
    >
      <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
      {t('passed')}
    </Badge>
  );
}

function IssuesList({ issues }: { issues: ValidationIssue[] }) {
  const t = useTranslations('validation');
  if (issues.length === 0) {
    return <p className="text-sm text-muted-foreground">{t('noIssues')}</p>;
  }
  return (
    <section>
      <h3 className="mb-2 text-sm font-medium">
        {t('issues', { count: issues.length })}
      </h3>
      <ul className="space-y-2">
        {issues.map((issue, idx) => (
          <li
            key={`${issue.code}-${idx}`}
            className="rounded-md border border-border bg-card p-3 text-sm"
            data-testid="validation-issue"
            data-issue-code={issue.code}
          >
            <div className="flex items-center gap-2">
              <SeverityChip severity={issue.severity} />
              <span className="font-mono text-xs text-muted-foreground">{issue.code}</span>
            </div>
            <p className="mt-1">{issue.message_en}</p>
            {issue.suggestion ? (
              <p className="mt-1 text-xs text-muted-foreground">{issue.suggestion}</p>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

function SeverityChip({ severity }: { severity: ValidationIssue['severity'] }) {
  const t = useTranslations('validation');
  const labels: Record<ValidationIssue['severity'], string> = {
    blocker: t('severityBlocker'),
    warning: t('severityWarning'),
    info: t('severityInfo'),
  };
  const styles: Record<ValidationIssue['severity'], string> = {
    blocker: 'bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300',
    warning: 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300',
    info: 'bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300',
  };
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-xs font-medium ${styles[severity]}`}
      data-testid="severity-chip"
      data-severity={severity}
    >
      {labels[severity]}
    </span>
  );
}

function HuntSection({ report }: { report: HuntReport | null }) {
  const t = useTranslations('validation.hunt');

  if (!report) {
    return null;
  }

  const blocked = report.recommendation === 'block_export';
  const claimPct =
    report.claim_check_pass_rate !== null
      ? Math.round(report.claim_check_pass_rate * 100)
      : null;

  return (
    <section
      className={`rounded-md border p-3 ${
        blocked
          ? 'border-red-200 bg-red-50/40 dark:border-red-900/60 dark:bg-red-950/20'
          : 'border-emerald-200 bg-emerald-50/40 dark:border-emerald-900/60 dark:bg-emerald-950/20'
      }`}
      data-testid="hunt-section"
      data-hunt-recommendation={report.recommendation}
    >
      <div className="flex items-center gap-2">
        {blocked ? (
          <AlertTriangle className="h-4 w-4 text-red-600" aria-hidden="true" />
        ) : (
          <CheckCircle2 className="h-4 w-4 text-emerald-600" aria-hidden="true" />
        )}
        <h3 className="text-sm font-medium">{t('title')}</h3>
      </div>
      <p className="mt-1 text-sm">{blocked ? t('blocked') : t('ok')}</p>
      <p className="mt-1 text-xs text-muted-foreground">
        {t('stats', {
          total: report.total_citations,
          verified: report.verified,
          fabricated: report.fabricated,
          notFound: report.not_found,
        })}
      </p>
      <p className="mt-1 text-xs text-muted-foreground">
        {claimPct === null ? t('claimRateUnknown') : t('claimRate', { pct: claimPct })}
      </p>
      {report.flagged_citations.length > 0 ? (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs font-medium">
            {t('flaggedList')} ({report.flagged_citations.length})
          </summary>
          <ul className="mt-2 space-y-1">
            {report.flagged_citations.map((c, idx) => (
              <li
                key={`${c.section}-${idx}`}
                className="rounded bg-background/60 px-2 py-1 font-mono text-xs"
                data-testid="flagged-citation"
              >
                {t('flaggedItem', {
                  section: c.section,
                  raw: c.raw_text,
                  status: c.status,
                })}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}

function ExportButton({
  proposalId,
  report,
}: {
  proposalId: string;
  report: ValidationReport;
}) {
  const t = useTranslations('validation.export');
  const [pending, setPending] = useState(false);
  const blocked = isExportBlocked(report);

  return (
    <div className="space-y-1">
      <Button
        variant="default"
        className="gap-2"
        disabled={blocked || pending}
        onClick={async () => {
          if (blocked) {
            return;
          }
          setPending(true);
          try {
            // The export endpoint is fire-and-forget (returns a Celery
            // job id). The polling UX is owned elsewhere — this PR's
            // scope is the gate, not the queue UX.
            await fetch(`/api/v1/proposals/${proposalId}/export`, {
              method: 'POST',
              credentials: 'include',
            });
          } finally {
            setPending(false);
          }
        }}
        data-testid="export-button"
        data-export-blocked={blocked}
      >
        <FileDown className="h-4 w-4" aria-hidden="true" />
        {blocked ? t('blocked') : t('ready')}
      </Button>
      {blocked ? (
        <p className="text-xs text-muted-foreground">{t('blockedReason')}</p>
      ) : null}
    </div>
  );
}
