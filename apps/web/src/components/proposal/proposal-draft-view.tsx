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

import { ProposalSectionEditor } from './tiptap/editor';

import type { ProposalSection } from './tiptap/editor';

/**
 * Draft renderer with the Faz 4 TipTap WYSIWYG editor mounted for
 * each writer section (excellence / impact / implementation). The
 * editor reads markdown on first render, hands the user the floating
 * AI bubble menu (rewrite / shorter / longer / translate), and emits
 * HTML on every change.
 *
 * Persistence: this view is the source of truth for the live HTML
 * but does NOT save automatically — the parent screen's save button
 * still drives the PATCH /proposals/{id} call. We surface the
 * unsaved-changes flag once the user touches any section.
 */
export function ProposalDraftView({ proposal }: { proposal: ProposalDetail }) {
  const t = useTranslations('proposalDetail');
  const tEditor = useTranslations('proposalEditor');
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
        <EditorSection
          proposalId={proposal.id}
          section="excellence"
          title="Excellence"
          body={draft.excellence_md}
          placeholder={tEditor('placeholder')}
        />
        <EditorSection
          proposalId={proposal.id}
          section="impact"
          title="Impact"
          body={draft.impact_md}
          placeholder={tEditor('placeholder')}
        />
        <EditorSection
          proposalId={proposal.id}
          section="implementation"
          title="Implementation"
          body={draft.implementation_md}
          placeholder={tEditor('placeholder')}
        />
      </CardContent>
    </Card>
  );
}

function EditorSection({
  proposalId,
  section,
  title,
  body,
  placeholder,
}: {
  proposalId: string;
  section: ProposalSection;
  title: string;
  body: string | undefined;
  placeholder: string;
}) {
  if (!body) return null;
  return (
    <section data-testid={`section-${title.toLowerCase()}`}>
      <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
        {title}
      </h3>
      <ProposalSectionEditor
        proposalId={proposalId}
        section={section}
        initialMarkdown={body}
        placeholder={placeholder}
      />
    </section>
  );
}
