'use client';

import { FileText, History, MessageSquare, ShieldCheck } from 'lucide-react';
import { useFormatter, useTranslations } from 'next-intl';
import { useState } from 'react';

import type { ProposalStatus } from '@bluedev/shared-types';

import { CommentsPanel } from '@/components/proposal/comments-panel';
import { GenerateProgress } from '@/components/proposal/generate-progress';
import { ProposalDraftView } from '@/components/proposal/proposal-draft-view';
import { ProvenancePanel } from '@/components/proposal/provenance-panel';
import { ValidationPanel } from '@/components/proposal/validation-panel';
import { VersionsPanel } from '@/components/proposal/versions-panel';
import { Badge } from '@/components/ui/badge';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useProposal } from '@/lib/api/queries';

/**
 * Sprint 4 MVP — proposal detail page.
 *
 * Wires:
 * - `useProposal(id)` for the live row (title, status, brief, draft,
 *   compliance metadata).
 * - `<GenerateProgress />` to fire the saga + show SSE events.
 * - `<ProposalDraftView />` to read the markdown sections + trigger
 *   DOCX / XLSX export.
 * - `<ValidationPanel />` (S3.D13.T1) for the export gate.
 * - `<VersionsPanel />` + `<CommentsPanel />` for collaboration.
 *
 * The TipTap editor lands later; until then "Draft" is read-only.
 */
export function ProposalEditorShell({
  proposalId,
  currentUserId,
}: {
  proposalId: string;
  currentUserId: string;
}) {
  const t = useTranslations('proposalDetail');
  const tValidation = useTranslations('validation');
  const tVersions = useTranslations('versions');
  const tComments = useTranslations('comments');
  const tProvenance = useTranslations('provenance');
  const tStatus = useTranslations('proposals.status');
  const format = useFormatter();
  const { data: proposal, isLoading, refetch } = useProposal(proposalId);
  const [activeTab, setActiveTab] = useState<string>('draft');

  if (isLoading || !proposal) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-12 w-1/2" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <div className="space-y-6 lg:col-span-2">
        <Card>
          <CardHeader>
            <CardTitle>{proposal.title ?? 'Untitled proposal'}</CardTitle>
            <CardDescription className="font-mono text-xs">{proposal.id}</CardDescription>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
              <div>
                <dt className="text-xs text-muted-foreground">
                  {t('metaProgramme')}
                </dt>
                <dd className="font-medium">{proposal.programme_id}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">
                  {t('metaStatus')}
                </dt>
                <dd>
                  <Badge variant="secondary" data-status={proposal.status}>
                    {tStatus(proposal.status as ProposalStatus)}
                  </Badge>
                </dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">
                  {t('metaCreated')}
                </dt>
                <dd>{format.relativeTime(new Date(proposal.created_at))}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">
                  {t('metaCost')}
                </dt>
                <dd className="tabular-nums">{proposal.llm_cost_usd.toFixed(2)}</dd>
              </div>
            </dl>
          </CardContent>
        </Card>

        <GenerateProgress
          proposalId={proposalId}
          generating={proposal.status === 'generating'}
          onCompleted={() => {
            refetch();
          }}
        />

        <ProposalDraftView proposal={proposal} />
      </div>
      <div className="lg:col-span-1">
        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList className="w-full">
            <TabsTrigger value="draft" className="flex-1 gap-2">
              <FileText className="h-4 w-4" />
              {t('tabs.draft')}
            </TabsTrigger>
            <TabsTrigger value="validation" className="flex-1 gap-2">
              <ShieldCheck className="h-4 w-4" />
              {tValidation('title')}
            </TabsTrigger>
            <TabsTrigger value="provenance" className="flex-1 gap-2">
              <FileText className="h-4 w-4" />
              {tProvenance('tabTitle')}
            </TabsTrigger>
            <TabsTrigger value="versions" className="flex-1 gap-2">
              <History className="h-4 w-4" />
              {tVersions('title')}
            </TabsTrigger>
            <TabsTrigger value="comments" className="flex-1 gap-2">
              <MessageSquare className="h-4 w-4" />
              {tComments('title')}
            </TabsTrigger>
          </TabsList>
          <TabsContent value="draft">
            <Card>
              <CardHeader>
                <CardTitle>{t('tabs.draft')}</CardTitle>
                <CardDescription>{t('draftEmpty')}</CardDescription>
              </CardHeader>
              <CardContent />
            </Card>
          </TabsContent>
          <TabsContent value="validation">
            <ValidationPanel proposalId={proposalId} />
          </TabsContent>
          <TabsContent value="provenance">
            <ProvenancePanel proposalId={proposalId} />
          </TabsContent>
          <TabsContent value="versions">
            <VersionsPanel proposalId={proposalId} />
          </TabsContent>
          <TabsContent value="comments">
            <CommentsPanel proposalId={proposalId} currentUserId={currentUserId} />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}
