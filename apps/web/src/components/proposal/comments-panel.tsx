'use client';

import { Check, CornerDownRight, Loader2, MessageSquare, Trash2 } from 'lucide-react';
import { useFormatter, useTranslations } from 'next-intl';
import { useMemo, useState } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { useToast } from '@/components/ui/use-toast';
import {
  useComments,
  useCreateComment,
  useDeleteComment,
  useResolveComment,
} from '@/lib/api/queries';
import type { CommentRecord } from '@bluedev/shared-types';

export function CommentsPanel({
  proposalId,
  currentUserId,
}: {
  proposalId: string;
  currentUserId: string;
}) {
  const t = useTranslations('comments');
  const tCommon = useTranslations('common');
  const format = useFormatter();
  const { toast } = useToast();
  const [includeResolved, setIncludeResolved] = useState(false);
  const { data, isLoading } = useComments(proposalId, { includeResolved });
  const create = useCreateComment(proposalId);
  const resolve = useResolveComment();
  const remove = useDeleteComment();

  const [body, setBody] = useState('');
  const [replyTo, setReplyTo] = useState<string | null>(null);
  const [replyBody, setReplyBody] = useState('');

  const grouped = useMemo(() => groupComments(data?.comments ?? []), [data]);

  async function onPost() {
    if (!body.trim()) return;
    try {
      await create.mutateAsync({ content: body.trim() });
      setBody('');
    } catch (err) {
      toast({ variant: 'destructive', description: (err as Error).message });
    }
  }

  async function onReply(parentId: string) {
    if (!replyBody.trim()) return;
    try {
      await create.mutateAsync({ content: replyBody.trim(), parent_id: parentId });
      setReplyBody('');
      setReplyTo(null);
    } catch (err) {
      toast({ variant: 'destructive', description: (err as Error).message });
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between text-lg">
          <span className="flex items-center gap-2">
            <MessageSquare className="h-5 w-5" />
            {t('title')}
          </span>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setIncludeResolved((v) => !v)}
            className="text-xs"
          >
            {t('showResolved')}
            {includeResolved && <Check className="h-3 w-3" />}
          </Button>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Textarea
            placeholder={t('placeholder')}
            value={body}
            onChange={(event) => setBody(event.target.value)}
            rows={3}
          />
          <div className="flex justify-end">
            <Button onClick={onPost} disabled={create.isPending || !body.trim()}>
              {create.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
              {t('post')}
            </Button>
          </div>
        </div>

        <Separator />

        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-20 w-full" />
          </div>
        ) : grouped.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('empty')}</p>
        ) : (
          <ul className="space-y-4">
            {grouped.map(({ root, replies }) => (
              <li key={root.id} className="space-y-2">
                <CommentRow
                  comment={root}
                  isAuthor={root.author_id === currentUserId}
                  format={format}
                  t={t}
                  tCommon={tCommon}
                  onReply={() => setReplyTo(root.id)}
                  onResolve={async () => {
                    try {
                      await resolve.mutateAsync(root.id);
                    } catch (err) {
                      toast({ variant: 'destructive', description: (err as Error).message });
                    }
                  }}
                  onDelete={async () => {
                    try {
                      await remove.mutateAsync(root.id);
                    } catch (err) {
                      toast({ variant: 'destructive', description: (err as Error).message });
                    }
                  }}
                />
                {replies.map((reply) => (
                  <div key={reply.id} className="ml-8 border-l-2 pl-3">
                    <CommentRow
                      comment={reply}
                      isAuthor={reply.author_id === currentUserId}
                      format={format}
                      t={t}
                      tCommon={tCommon}
                      onResolve={async () => {
                        try {
                          await resolve.mutateAsync(reply.id);
                        } catch (err) {
                          toast({ variant: 'destructive', description: (err as Error).message });
                        }
                      }}
                      onDelete={async () => {
                        try {
                          await remove.mutateAsync(reply.id);
                        } catch (err) {
                          toast({ variant: 'destructive', description: (err as Error).message });
                        }
                      }}
                    />
                  </div>
                ))}
                {replyTo === root.id && (
                  <div className="ml-8 flex items-start gap-2 border-l-2 pl-3">
                    <CornerDownRight className="mt-2 h-4 w-4 text-muted-foreground" />
                    <div className="flex-1 space-y-2">
                      <Textarea
                        placeholder={t('replyPlaceholder')}
                        value={replyBody}
                        onChange={(event) => setReplyBody(event.target.value)}
                        rows={2}
                      />
                      <div className="flex justify-end gap-2">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => {
                            setReplyTo(null);
                            setReplyBody('');
                          }}
                        >
                          {tCommon('cancel')}
                        </Button>
                        <Button
                          size="sm"
                          onClick={() => onReply(root.id)}
                          disabled={create.isPending || !replyBody.trim()}
                        >
                          {t('post')}
                        </Button>
                      </div>
                    </div>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function groupComments(comments: CommentRecord[]) {
  const map = new Map<string, { root: CommentRecord; replies: CommentRecord[] }>();
  for (const c of comments) {
    if (c.parent_id === null) {
      const existing = map.get(c.id);
      if (existing) existing.root = c;
      else map.set(c.id, { root: c, replies: [] });
    }
  }
  for (const c of comments) {
    if (c.parent_id !== null) {
      const parent = map.get(c.parent_id);
      if (parent) parent.replies.push(c);
    }
  }
  return Array.from(map.values()).sort(
    (a, b) => new Date(a.root.created_at).getTime() - new Date(b.root.created_at).getTime(),
  );
}

interface CommentRowProps {
  comment: CommentRecord;
  isAuthor: boolean;
  format: ReturnType<typeof useFormatter>;
  t: ReturnType<typeof useTranslations>;
  tCommon: ReturnType<typeof useTranslations>;
  onReply?: () => void;
  onResolve: () => Promise<void>;
  onDelete: () => Promise<void>;
}

function CommentRow({
  comment,
  isAuthor,
  format,
  t,
  onReply,
  onResolve,
  onDelete,
}: CommentRowProps) {
  return (
    <div className={comment.resolved ? 'opacity-60' : ''}>
      <div className="flex items-start justify-between gap-2">
        <div className="text-sm">
          <p>{comment.content}</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {comment.author_id.slice(0, 8)} ·{' '}
            {format.dateTime(new Date(comment.created_at), 'short')}
            {comment.section && (
              <span className="ml-1">{t('section', { section: comment.section })}</span>
            )}
            {comment.resolved && (
              <Badge variant="secondary" className="ml-2 text-[10px]">
                {t('resolved')}
              </Badge>
            )}
          </p>
        </div>
        <div className="flex items-center gap-1">
          {onReply && !comment.resolved && (
            <Button variant="ghost" size="sm" onClick={onReply}>
              {t('reply')}
            </Button>
          )}
          {!comment.resolved && (
            <Button variant="ghost" size="sm" onClick={onResolve}>
              <Check className="h-3.5 w-3.5" />
              {t('resolve')}
            </Button>
          )}
          {isAuthor && (
            <Button variant="ghost" size="icon" onClick={onDelete}>
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
