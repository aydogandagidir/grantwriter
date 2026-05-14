'use client';

import { Compass, Loader2, SearchIcon, SlidersHorizontal } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useMemo, useState } from 'react';

import type {
  CallSearchFilters,
  CallSortKey,
  CallStatus,
} from '@bluedev/shared-types';

import { CallCard } from '@/app/[locale]/(app)/calls/call-card';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { useCalls, useProgrammes } from '@/lib/api/queries';

const PAGE_SIZE = 24;

const SORT_OPTIONS: CallSortKey[] = ['deadline', 'budget', 'recency', 'relevance'];
const STATUS_OPTIONS: (CallStatus | 'any')[] = ['any', 'open', 'closing_soon', 'closed'];

/**
 * /calls browse page — client component.
 *
 * V1 surfaces the high-signal filters (search, programme, status, sort)
 * directly on the page. The richer faceted filters (sectors, eligibility,
 * geography, TRL, budget range, dates) live behind a follow-up "Advanced"
 * drawer so the default view stays clean for first-time users.
 *
 * URL-state sync (so users can share filtered links) lands separately.
 * Page state is reset to 0 every time a filter changes, otherwise
 * paginating to page 4 then changing programme would land the user on
 * an empty page 4 of the new (smaller) result set.
 */
export function CallsBrowser() {
  const t = useTranslations('calls');
  const tFilters = useTranslations('calls.filters');
  const tSort = useTranslations('calls.sort');
  const tStatus = useTranslations('calls.status');
  const tPagination = useTranslations('calls.pagination');

  const [q, setQ] = useState('');
  const [programmeId, setProgrammeId] = useState<string>('all');
  const [statusFilter, setStatusFilter] = useState<CallStatus | 'any'>('any');
  const [sort, setSort] = useState<CallSortKey>('deadline');
  const [page, setPage] = useState(0);

  const filters: CallSearchFilters = useMemo(
    () => ({
      q: q.trim() || undefined,
      programme_ids: programmeId === 'all' ? undefined : [programmeId],
      status_filter: statusFilter === 'any' ? undefined : statusFilter,
      sort,
      limit: PAGE_SIZE,
      offset: page * PAGE_SIZE,
    }),
    [q, programmeId, statusFilter, sort, page],
  );

  const { data, isLoading, isError, isFetching, refetch } = useCalls(filters);
  const { data: programmesData } = useProgrammes();
  const programmes = programmesData?.programmes ?? [];

  const total = data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const currentPage = page + 1;

  function clearAll() {
    setQ('');
    setProgrammeId('all');
    setStatusFilter('any');
    setSort('deadline');
    setPage(0);
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">{t('title')}</h1>
          <p className="text-muted-foreground">{t('subtitle')}</p>
        </div>
        {isFetching && !isLoading ? (
          <span className="flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="h-3 w-3 animate-spin" />
            {t('loading')}
          </span>
        ) : null}
      </header>

      <Card>
        <CardHeader className="space-y-1">
          <div className="flex items-center gap-2">
            <SlidersHorizontal className="h-4 w-4 text-muted-foreground" />
            <CardTitle className="text-base">{tFilters('title')}</CardTitle>
          </div>
          <CardDescription>{t('resultCount', { count: total })}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-3 md:grid-cols-12">
            <div className="md:col-span-4">
              <Label htmlFor="calls-search" className="sr-only">
                {t('searchPlaceholder')}
              </Label>
              <div className="relative">
                <SearchIcon className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  id="calls-search"
                  data-testid="calls-search"
                  placeholder={t('searchPlaceholder')}
                  className="pl-9"
                  value={q}
                  onChange={(event) => {
                    setQ(event.target.value);
                    setPage(0);
                  }}
                />
              </div>
            </div>

            <div className="md:col-span-3">
              <Label htmlFor="calls-programme">{tFilters('programme')}</Label>
              <Select
                value={programmeId}
                onValueChange={(value) => {
                  setProgrammeId(value);
                  setPage(0);
                }}
              >
                <SelectTrigger id="calls-programme" data-testid="calls-programme">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">— —</SelectItem>
                  {programmes.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.name_en}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="md:col-span-2">
              <Label htmlFor="calls-status">{tFilters('status')}</Label>
              <Select
                value={statusFilter}
                onValueChange={(value) => {
                  setStatusFilter(value as CallStatus | 'any');
                  setPage(0);
                }}
              >
                <SelectTrigger id="calls-status" data-testid="calls-status">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {STATUS_OPTIONS.map((s) => (
                    <SelectItem key={s} value={s}>
                      {s === 'any' ? tFilters('anyStatus') : tStatus(s as CallStatus)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="md:col-span-2">
              <Label htmlFor="calls-sort">{tSort('label')}</Label>
              <Select
                value={sort}
                onValueChange={(value) => {
                  setSort(value as CallSortKey);
                  setPage(0);
                }}
              >
                <SelectTrigger id="calls-sort" data-testid="calls-sort">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SORT_OPTIONS.map((s) => (
                    <SelectItem key={s} value={s}>
                      {tSort(s)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="flex items-end md:col-span-1">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={clearAll}
                className="w-full"
              >
                {tFilters('clearAll')}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {isLoading ? (
        <div
          className="grid gap-4 md:grid-cols-2 xl:grid-cols-3"
          data-testid="calls-loading"
        >
          {Array.from({ length: 6 }).map((_, idx) => (
            <Skeleton key={idx} className="h-56 w-full" />
          ))}
        </div>
      ) : isError ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 py-12 text-center">
            <p className="text-sm text-destructive">{t('errorTitle')}</p>
            <Button variant="outline" onClick={() => refetch()}>
              {t('errorRetry')}
            </Button>
          </CardContent>
        </Card>
      ) : (data?.calls.length ?? 0) === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 py-12 text-center">
            <Compass className="h-8 w-8 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">{t('empty')}</p>
            <Button variant="outline" onClick={clearAll}>
              {tFilters('clearAll')}
            </Button>
          </CardContent>
        </Card>
      ) : (
        <>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3" data-testid="calls-grid">
            {data!.calls.map((call) => (
              <CallCard key={call.id} call={call} />
            ))}
          </div>
          {pageCount > 1 ? (
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs text-muted-foreground">
                {tPagination('page', { current: currentPage, total: pageCount })}
              </span>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page === 0}
                  onClick={() => setPage((p) => Math.max(0, p - 1))}
                >
                  {tPagination('previous')}
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={currentPage >= pageCount}
                  onClick={() => setPage((p) => p + 1)}
                >
                  {tPagination('next')}
                </Button>
              </div>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
