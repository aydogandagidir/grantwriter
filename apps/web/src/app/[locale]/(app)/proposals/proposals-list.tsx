'use client';

import { FileText, Loader2, PlusCircle } from 'lucide-react';
import { useFormatter, useTranslations } from 'next-intl';
import { useRouter } from 'next/navigation';

import type { ProposalStatus } from '@bluedev/shared-types';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Link } from '@/i18n/navigation';
import { useProposals } from '@/lib/api/queries';

/**
 * Sprint 4 MVP — proposals list page. Replaces the placeholder card
 * that shipped in PR #9. Wired to `GET /api/v1/proposals` via the
 * `useProposals` TanStack hook.
 */
export function ProposalsList() {
  const t = useTranslations('proposals');
  const tStatus = useTranslations('proposals.status');
  const format = useFormatter();
  const router = useRouter();
  const { data, isLoading, isError } = useProposals();

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">{t('title')}</h1>
          <p className="text-muted-foreground">{t('subtitle')}</p>
        </div>
        <Button asChild>
          <Link href="/proposals/new">
            <PlusCircle className="h-4 w-4" />
            {t('newProposal')}
          </Link>
        </Button>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>{t('title')}</CardTitle>
          <CardDescription>{t('subtitle')}</CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-2" data-testid="proposals-loading">
              {Array.from({ length: 4 }).map((_, idx) => (
                <Skeleton key={idx} className="h-10 w-full" />
              ))}
            </div>
          ) : isError ? (
            <p className="text-sm text-destructive">
              {/* Translation reuse — the generic error string from `common`
                  works here too; the page-specific empty state lives below. */}
              {t('empty')}
            </p>
          ) : data && data.proposals.length === 0 ? (
            <div className="flex flex-col items-center gap-3 py-12 text-center">
              <FileText className="h-8 w-8 text-muted-foreground" />
              <p className="text-sm text-muted-foreground">{t('empty')}</p>
              <Button asChild variant="outline">
                <Link href="/proposals/new">
                  <PlusCircle className="h-4 w-4" />
                  {t('newProposal')}
                </Link>
              </Button>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t('columns.title')}</TableHead>
                  <TableHead>{t('columns.programme')}</TableHead>
                  <TableHead>{t('columns.status')}</TableHead>
                  <TableHead>{t('columns.created')}</TableHead>
                  <TableHead className="text-right">{t('columns.cost')}</TableHead>
                  <TableHead className="text-right" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {data!.proposals.map((proposal) => (
                  <TableRow
                    key={proposal.id}
                    className="cursor-pointer"
                    data-testid="proposal-row"
                    data-proposal-id={proposal.id}
                    onClick={() => router.push(`/proposals/${proposal.id}`)}
                  >
                    <TableCell className="font-medium">
                      {proposal.title ?? t('untitled')}
                    </TableCell>
                    <TableCell>{proposal.programme_id}</TableCell>
                    <TableCell>
                      <StatusBadge status={proposal.status} label={tStatus(proposal.status)} />
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {format.relativeTime(new Date(proposal.created_at))}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {proposal.llm_cost_usd.toFixed(2)}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button asChild variant="ghost" size="sm">
                        <Link href={`/proposals/${proposal.id}`}>
                          {t('openProposal')}
                        </Link>
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function StatusBadge({ status, label }: { status: ProposalStatus; label: string }) {
  // Map the 11 status values to the badge variants we have available.
  // "Generating" gets a spinner so the dashboard hints when a saga is
  // mid-flight without polling per row.
  if (status === 'generating') {
    return (
      <Badge variant="secondary" className="gap-1">
        <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
        {label}
      </Badge>
    );
  }
  if (status === 'draft_complete' || status === 'validated' || status === 'funded') {
    return <Badge>{label}</Badge>;
  }
  if (status === 'rejected' || status === 'archived') {
    return <Badge variant="outline">{label}</Badge>;
  }
  return <Badge variant="secondary">{label}</Badge>;
}
