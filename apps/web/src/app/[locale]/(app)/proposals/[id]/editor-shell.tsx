'use client';

import { FileText, History, MessageSquare, ShieldCheck } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useState } from 'react';

import { CommentsPanel } from '@/components/proposal/comments-panel';
import { ProposalEditor } from '@/components/proposal/proposal-editor';
import { ProvenancePanel } from '@/components/proposal/provenance-panel';
import { ValidationPanel } from '@/components/proposal/validation-panel';
import { VersionsPanel } from '@/components/proposal/versions-panel';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useProposal } from '@/lib/api/queries';

const SECTIONS = ['excellence', 'impact', 'implementation'] as const;
type EditableSection = (typeof SECTIONS)[number];

/**
 * Stub proposal editor used to host the Versions + Comments + Validation
 * panels until the real TipTap editor lands. Renders a placeholder card
 * on the left and the collaboration panels in a tabbed right rail.
 *
 * S3.D13.T1 added the "Validation" tab — it surfaces the Hallucination
 * Hunter recommendation + Compliance Reviewer issues + a unified export
 * button that disables when either gate trips.
 */
export function ProposalEditorShell({
  proposalId,
  currentUserId,
}: {
  proposalId: string;
  currentUserId: string;
}) {
  const tNav = useTranslations('nav');
  const tVersions = useTranslations('versions');
  const tComments = useTranslations('comments');
  const tValidation = useTranslations('validation');
  const tProvenance = useTranslations('provenance');
  const tSections = useTranslations('sections');
  // Pull the proposal so the editor can pre-fill from the saga's
  // writers. The query is keyed on ``proposalId`` so a swap between
  // proposals (e.g. via the sidebar dropdown once we add one) just
  // re-fetches; the editor's transaction listener handles re-attach.
  const proposal = useProposal(proposalId);
  // Active section drives both the editor's content + the provenance
  // items query inside ProposalEditor. The TipTap instance gets
  // ``key={section}`` further down so it re-mounts on switch — the
  // editor's debounce flushes any pending edits in the unmount
  // cleanup so we don't drop work when the user changes tabs.
  const [section, setSection] = useState<EditableSection>('excellence');
  const sectionMarkdown =
    proposal.data?.draft[`${section}_md` as `${EditableSection}_md`] ?? '';

  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <div className="space-y-6 lg:col-span-2">
        <Card>
          <CardHeader>
            <CardTitle>{tNav('proposals')}</CardTitle>
            <CardDescription className="font-mono text-xs">{proposalId}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <Tabs
              value={section}
              onValueChange={(value) => setSection(value as EditableSection)}
            >
              <TabsList className="w-full" aria-label={tSections('aria')}>
                {SECTIONS.map((name) => (
                  <TabsTrigger key={name} value={name} className="flex-1">
                    {tSections(name)}
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
            {/*
             * ``key`` forces a remount when the user switches sections so the
             * provenance attacher re-runs on the new markdown + the items hook
             * keys on the new ``section`` value. The unmount cleanup in
             * ``proposal-editor.tsx`` fires the pending debounce so in-flight
             * edits are persisted before the swap.
             */}
            <ProposalEditor
              key={section}
              proposalId={proposalId}
              section={section}
              initialMarkdown={sectionMarkdown}
            />
          </CardContent>
        </Card>
      </div>
      <div className="lg:col-span-1">
        <Tabs defaultValue="validation">
          <TabsList className="w-full">
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
