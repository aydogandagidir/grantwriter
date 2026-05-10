'use client';

import { History, MessageSquare } from 'lucide-react';
import { useTranslations } from 'next-intl';

import { CommentsPanel } from '@/components/proposal/comments-panel';
import { VersionsPanel } from '@/components/proposal/versions-panel';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

/**
 * Stub proposal editor used to host the Versions + Comments panels until
 * the real TipTap editor lands. Renders a placeholder card on the left
 * and the collaboration panels in a tabbed right rail.
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

  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <div className="space-y-6 lg:col-span-2">
        <Card>
          <CardHeader>
            <CardTitle>{tNav('proposals')}</CardTitle>
            <CardDescription className="font-mono text-xs">{proposalId}</CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground">
              The TipTap editor lands in a follow-up sprint. The Sprint 3 backend
              collaboration features (versions + comments) are wired up on the
              right.
            </p>
          </CardContent>
        </Card>
      </div>
      <div className="lg:col-span-1">
        <Tabs defaultValue="versions">
          <TabsList className="w-full">
            <TabsTrigger value="versions" className="flex-1 gap-2">
              <History className="h-4 w-4" />
              {tVersions('title')}
            </TabsTrigger>
            <TabsTrigger value="comments" className="flex-1 gap-2">
              <MessageSquare className="h-4 w-4" />
              {tComments('title')}
            </TabsTrigger>
          </TabsList>
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
