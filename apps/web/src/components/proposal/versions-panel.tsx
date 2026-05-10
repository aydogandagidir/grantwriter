'use client';

import { History, Loader2, RotateCcw, Save } from 'lucide-react';
import { useFormatter, useTranslations } from 'next-intl';
import { useState } from 'react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Separator } from '@/components/ui/separator';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/components/ui/use-toast';
import { useCreateVersion, useRestoreVersion, useVersions } from '@/lib/api/queries';
import type { VersionSummary } from '@bluedev/shared-types';

export function VersionsPanel({ proposalId }: { proposalId: string }) {
  const t = useTranslations('versions');
  const tCommon = useTranslations('common');
  const format = useFormatter();
  const { toast } = useToast();
  const { data, isLoading } = useVersions(proposalId);
  const create = useCreateVersion(proposalId);
  const restore = useRestoreVersion(proposalId);

  const [comment, setComment] = useState('');
  const [restoreTarget, setRestoreTarget] = useState<VersionSummary | null>(null);

  async function onCreate() {
    try {
      await create.mutateAsync(comment || undefined);
      const newVersion = (data?.versions[0]?.version_number ?? 0) + 1;
      toast({ title: t('createdToast', { n: newVersion }) });
      setComment('');
    } catch (err) {
      toast({ variant: 'destructive', description: (err as Error).message });
    }
  }

  async function onRestore() {
    if (!restoreTarget) return;
    try {
      await restore.mutateAsync(restoreTarget.version_number);
      const newVersion = (data?.versions[0]?.version_number ?? 0) + 1;
      toast({
        title: t('restoredToast', { n: restoreTarget.version_number, newN: newVersion }),
      });
      setRestoreTarget(null);
    } catch (err) {
      toast({ variant: 'destructive', description: (err as Error).message });
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-lg">
          <History className="h-5 w-5" />
          {t('title')}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-col gap-2 md:flex-row">
          <Input
            placeholder={t('createPlaceholder')}
            value={comment}
            onChange={(event) => setComment(event.target.value)}
          />
          <Button onClick={onCreate} disabled={create.isPending}>
            {create.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Save className="h-4 w-4" />
            )}
            {t('create')}
          </Button>
        </div>

        <Separator />

        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : data && data.versions.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('empty')}</p>
        ) : (
          <ul className="space-y-2">
            {data?.versions.map((version) => (
              <li
                key={version.id}
                className="flex items-center justify-between gap-3 rounded-md border p-3 text-sm"
              >
                <div className="space-y-0.5">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs font-semibold">
                      {t('version', { n: version.version_number })}
                    </span>
                    {version.comment && (
                      <span className="text-muted-foreground">{version.comment}</span>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {format.dateTime(new Date(version.created_at), 'short')}
                    {version.created_by && ` · ${version.created_by.slice(0, 8)}`}
                  </p>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setRestoreTarget(version)}
                  disabled={restore.isPending}
                >
                  <RotateCcw className="h-4 w-4" />
                  {t('restore')}
                </Button>
              </li>
            ))}
          </ul>
        )}

        <Dialog
          open={restoreTarget !== null}
          onOpenChange={(o) => !o && setRestoreTarget(null)}
        >
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{t('restore')}</DialogTitle>
              <DialogDescription>
                {restoreTarget &&
                  t('restoreConfirm', { n: restoreTarget.version_number })}
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" onClick={() => setRestoreTarget(null)}>
                {tCommon('cancel')}
              </Button>
              <Button onClick={onRestore} disabled={restore.isPending}>
                {restore.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                {t('restore')}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </CardContent>
    </Card>
  );
}
