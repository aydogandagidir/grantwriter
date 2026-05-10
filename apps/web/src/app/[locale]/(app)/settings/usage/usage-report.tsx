'use client';

import { useFormatter, useTranslations } from 'next-intl';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useUsage } from '@/lib/api/queries';

export function UsageReport() {
  const t = useTranslations('usage');
  const format = useFormatter();
  const { data, isLoading } = useUsage();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">{t('title')}</h1>
        <p className="text-muted-foreground">{t('subtitle')}</p>
      </div>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <KpiCard
          label={t('totalCost')}
          value={isLoading ? null : `$${(data?.total_cost_usd ?? 0).toFixed(2)}`}
        />
        <KpiCard
          label={t('totalCalls')}
          value={isLoading ? null : format.number(data?.call_count ?? 0)}
        />
        <KpiCard
          label={t('totalInputTokens')}
          value={isLoading ? null : format.number(data?.total_input_tokens ?? 0)}
        />
        <KpiCard
          label={t('byokShare')}
          value={isLoading ? null : format.number(data?.byok_call_count ?? 0)}
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-xl">{t('byPeriod')}</CardTitle>
          <CardDescription>{data?.by_period.length ?? 0}</CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 3 }).map((_, idx) => (
                <Skeleton key={idx} className="h-10 w-full" />
              ))}
            </div>
          ) : data && data.by_period.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t('empty')}</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t('period')}</TableHead>
                  <TableHead className="text-right">{t('cost')}</TableHead>
                  <TableHead className="text-right">{t('calls')}</TableHead>
                  <TableHead className="text-right">{t('totalInputTokens')}</TableHead>
                  <TableHead className="text-right">{t('totalOutputTokens')}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data?.by_period.map((row) => (
                  <TableRow key={row.period_start}>
                    <TableCell>{format.dateTime(new Date(row.period_start), 'short')}</TableCell>
                    <TableCell className="text-right">${row.total_cost_usd.toFixed(2)}</TableCell>
                    <TableCell className="text-right">{format.number(row.call_count)}</TableCell>
                    <TableCell className="text-right">
                      {format.number(row.total_input_tokens)}
                    </TableCell>
                    <TableCell className="text-right">
                      {format.number(row.total_output_tokens)}
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

function KpiCard({ label, value }: { label: string; value: string | null }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardDescription>{label}</CardDescription>
      </CardHeader>
      <CardContent>
        {value === null ? (
          <Skeleton className="h-8 w-24" />
        ) : (
          <span className="text-2xl font-bold">{value}</span>
        )}
      </CardContent>
    </Card>
  );
}
