'use client';

import { Download, FileText, Loader2 } from 'lucide-react';
import { useTranslations } from 'next-intl';

import type { ProposalDetail } from '@bluedev/shared-types';

import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { useToast } from '@/components/ui/use-toast';
import { useExportProposal } from '@/lib/api/queries';

/**
 * Read-only draft renderer (Sprint 4 MVP). Shows the three writer
 * outputs (excellence_md / impact_md / implementation_md) as
 * `<pre className="whitespace-pre-wrap">` blocks. The real TipTap
 * editor lands in a follow-up sprint; until then this surface is
 * good enough for pilot operators to read + spot-check the output.
 *
 * Export button enqueues a DOCX task (XLSX for HE Lump Sum is one
 * dropdown away — kept simple for MVP).
 */
export function ProposalDraftView({ proposal }: { proposal: ProposalDetail }) {
  const t = useTranslations('proposalDetail');
  const { toast } = useToast();
  const exportMutation = useExportProposal(proposal.id);

  const draft = proposal.draft as {
    excellence_md?: string;
    impact_md?: string;
    implementation_md?: string;
  };

  const hasDraft = Boolean(
    draft.excellence_md || draft.impact_md || draft.implementation_md,
  );

  if (!hasDraft) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{t('tabs.draft')}</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">{t('draftEmpty')}</p>
        </CardContent>
      </Card>
    );
  }

  const triggerExport = async (format: 'docx' | 'xlsx') => {
    try {
      const result = await exportMutation.mutateAsync(format);
      toast({ title: t('exportQueued', { jobId: result.job_id }) });
    } catch {
      // ApiError thrown → toaster picks it up via the global handler
      // when one exists; for MVP we just swallow + leave the button
      // re-enabled.
    }
  };

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-3">
        <div>
          <CardTitle>{t('tabs.draft')}</CardTitle>
          <CardDescription className="font-mono text-xs">
            {proposal.id}
          </CardDescription>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={exportMutation.isPending}
            onClick={() => triggerExport('docx')}
            className="gap-2"
            data-testid="export-docx"
          >
            {exportMutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <Download className="h-4 w-4" aria-hidden="true" />
            )}
            {t('exportDocx')}
          </Button>
          {proposal.programme_id === 'horizon_eu_ria' ? (
            <Button
              variant="outline"
              size="sm"
              disabled={exportMutation.isPending}
              onClick={() => triggerExport('xlsx')}
              className="gap-2"
            >
              <FileText className="h-4 w-4" aria-hidden="true" />
              {t('exportXlsx')}
            </Button>
          ) : null}
        </div>
      </CardHeader>
      <CardContent className="space-y-6">
        <Section title="Excellence" body={draft.excellence_md} />
        <Section title="Impact" body={draft.impact_md} />
        <Section title="Implementation" body={draft.implementation_md} />
      </CardContent>
    </Card>
  );
}

function Section({ title, body }: { title: string; body: string | undefined }) {
  if (!body) {
    return null;
  }
  return (
    <section>
      <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
        {title}
      </h3>
      <pre
        className="whitespace-pre-wrap rounded-md border bg-card p-4 text-sm leading-relaxed"
        data-testid={`section-${title.toLowerCase()}`}
      >
        {body}
      </pre>
    </section>
  );
}
