'use client';

import { useFormatter, useTranslations } from 'next-intl';
import { useState } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
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
import { useAuditLog } from '@/lib/api/queries';

export function AuditTable() {
  const t = useTranslations('audit');
  const format = useFormatter();
  const [limit, setLimit] = useState(50);
  const { data, isLoading } = useAuditLog({ limit });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">{t('title')}</h1>
        <p className="text-muted-foreground">{t('subtitle')}</p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle className="text-xl">{t('title')}</CardTitle>
          <CardDescription>{data?.events.length ?? 0}</CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 5 }).map((_, idx) => (
                <Skeleton key={idx} className="h-10 w-full" />
              ))}
            </div>
          ) : data && data.events.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t('empty')}</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t('action')}</TableHead>
                  <TableHead>{t('actor')}</TableHead>
                  <TableHead>{t('resource')}</TableHead>
                  <TableHead>{t('when')}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data?.events.map((event) => (
                  <TableRow key={event.id}>
                    <TableCell>
                      <Badge variant="outline" className="font-mono text-xs">
                        {event.action}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {event.user_id ? event.user_id.slice(0, 8) : '—'}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {event.resource_type ?? '—'}
                      {event.resource_id && (
                        <span className="ml-1 font-mono text-xs">
                          {event.resource_id.slice(0, 8)}
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {format.dateTime(new Date(event.created_at), 'short')}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          {data?.has_more && (
            <div className="mt-4 text-center">
              <Button variant="outline" onClick={() => setLimit((l) => l + 50)}>
                {t('loadMore')}
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
