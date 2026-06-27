'use client';

import { Check, Copy } from 'lucide-react';
import { useLocale, useTranslations } from 'next-intl';
import { useCallback, useMemo, useState } from 'react';

import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { useProvenanceStats } from '@/lib/api/queries';
import { cn } from '@/lib/utils';

import { formatDisclosure, labelFor, summarisePercentages } from './provenance-disclosure';

interface ProvenanceBarProps {
  value: number;
  label: string;
  className?: string;
}

/** Minimal inline bar — no separate shadcn dep. */
function ProvenanceBar({ value, label, className }: ProvenanceBarProps) {
  const safe = Math.max(0, Math.min(100, value));
  return (
    <div
      role="progressbar"
      aria-valuenow={safe}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label}
      className={cn('h-2 w-full rounded-full bg-muted', className)}
    >
      <div
        className="h-full rounded-full bg-primary transition-all"
        style={{ width: `${safe}%` }}
      />
    </div>
  );
}

interface ProvenancePanelProps {
  proposalId: string;
}

/**
 * Sidebar that shows per-source provenance breakdown + the AI Use
 * disclosure block. The disclosure is the text the author pastes into
 * Horizon Europe Part B page 32 (or the equivalent TÜBİTAK form) so
 * we keep it copy-friendly: a single clipboard call ships the whole
 * formatted block.
 *
 * The component intentionally has no edit affordances — provenance
 * mutations flow through the editor (downgrade on edit) + the
 * agents (set on insert). This panel is read-only.
 */
export function ProvenancePanel({ proposalId }: ProvenancePanelProps) {
  const t = useTranslations('provenance');
  const locale = useLocale();
  const lang: 'en' | 'tr' = locale.startsWith('tr') ? 'tr' : 'en';

  const stats = useProvenanceStats(proposalId);
  const [copied, setCopied] = useState(false);

  const summary = useMemo(() => {
    if (!stats.data) return null;
    return summarisePercentages(stats.data);
  }, [stats.data]);

  const disclosure = useMemo(() => {
    if (!stats.data) return '';
    return formatDisclosure({ stats: stats.data, lang });
  }, [stats.data, lang]);

  const handleCopy = useCallback(async () => {
    if (!disclosure) return;
    try {
      await navigator.clipboard.writeText(disclosure);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Browsers without clipboard support land here — fall back to
      // selecting the textarea so the user can hit ⌘C themselves.
      setCopied(false);
    }
  }, [disclosure]);

  if (stats.isPending) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{t('title')}</CardTitle>
        </CardHeader>
        <CardContent aria-busy="true" aria-live="polite">
          {t('loading')}
        </CardContent>
      </Card>
    );
  }

  if (stats.isError) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{t('title')}</CardTitle>
          <CardDescription>{t('loadError')}</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  if (!stats.data || stats.data.total === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{t('title')}</CardTitle>
          <CardDescription>{t('emptyDescription')}</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('title')}</CardTitle>
        <CardDescription>
          {t('totalSentences', { count: stats.data.total })}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <ul className="space-y-3" aria-label={t('breakdownAria')}>
          {stats.data.per_source.map((row) => {
            const pct = summary?.percentages[row.source] ?? 0;
            return (
              <li key={row.source} className="space-y-1">
                <div className="flex items-center justify-between text-sm">
                  <span>{labelFor(row.source, lang)}</span>
                  <span className="font-mono text-xs text-muted-foreground">
                    {row.count} • {pct}%
                  </span>
                </div>
                <ProvenanceBar
                  value={pct}
                  label={`${labelFor(row.source, lang)} ${pct}%`}
                />
              </li>
            );
          })}
        </ul>

        <details className="rounded border bg-muted/30 p-3 text-sm">
          <summary className="cursor-pointer font-medium">
            {t('disclosureHeading')}
          </summary>
          <pre
            className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap rounded bg-background p-3 text-xs"
            data-testid="provenance-disclosure-text"
          >
            {disclosure}
          </pre>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="mt-3"
            onClick={handleCopy}
            disabled={!disclosure}
            aria-live="polite"
          >
            {copied ? (
              <>
                <Check className="mr-2 h-4 w-4" /> {t('copied')}
              </>
            ) : (
              <>
                <Copy className="mr-2 h-4 w-4" /> {t('copy')}
              </>
            )}
          </Button>
        </details>
      </CardContent>
    </Card>
  );
}
