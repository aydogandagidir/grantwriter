'use client';

import { CheckCircle2, Loader2, Play, XCircle } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useEffect, useState } from 'react';

import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { env } from '@/lib/env';
import { useGenerateProposal } from '@/lib/api/queries';

interface SsaEvent {
  type: string;
  payload: Record<string, unknown>;
}

/**
 * Generate + live progress component (Sprint 4 MVP).
 *
 * - "Generate" button POSTs `/proposals/{id}/generate` (rate-limited 10/60s).
 * - Returns a stream URL pointing at `/proposals/{id}/stream` (SSE).
 * - We subscribe via `EventSource`, render each agent event as a
 *   timeline row, and stop the connection when we see ``completed``
 *   or ``error``.
 *
 * The TipTap editor lands later — Sprint 4 ships read-only markdown
 * rendering of the saga's output (see `proposal-draft-view.tsx`).
 */
export function GenerateProgress({
  proposalId,
  generating,
  onCompleted,
}: {
  proposalId: string;
  generating: boolean;
  onCompleted: () => void;
}) {
  const t = useTranslations('generateProgress');
  const tDetail = useTranslations('proposalDetail');
  const generate = useGenerateProposal(proposalId);
  const [streamUrl, setStreamUrl] = useState<string | null>(null);
  const [events, setEvents] = useState<SsaEvent[]>([]);
  const [closed, setClosed] = useState(false);

  // Open SSE when we get a stream URL (after the POST returns) OR when
  // the proposal already shows `status=generating` (e.g. user reloaded
  // mid-flight; we want to pick up where we left off).
  useEffect(() => {
    if (!streamUrl && generating) {
      // The proposal is already generating but we don't have a stream
      // URL handle — compose one from the API origin.
      setStreamUrl(`${env.apiUrl}/api/v1/proposals/${proposalId}/stream`);
    }
  }, [streamUrl, generating, proposalId]);

  useEffect(() => {
    if (!streamUrl) {
      return;
    }
    const source = new EventSource(streamUrl, { withCredentials: true });
    setClosed(false);

    source.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as SsaEvent;
        setEvents((prev) => [...prev, data]);
        if (data.type === 'completed' || data.type === 'error') {
          source.close();
          setClosed(true);
          onCompleted();
        }
      } catch {
        // Malformed event — ignore; SSE retains the connection.
      }
    };

    source.onerror = () => {
      source.close();
      setClosed(true);
    };

    return () => {
      source.close();
    };
  }, [streamUrl, onCompleted]);

  const onClickGenerate = async () => {
    const result = await generate.mutateAsync();
    setStreamUrl(`${env.apiUrl}${result.stream_url}`);
    setEvents([]);
    setClosed(false);
  };

  const pending = generate.isPending || (generating && !closed);

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('title')}</CardTitle>
        <CardDescription>{t('subtitle')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <Button
          onClick={onClickGenerate}
          disabled={pending}
          className="gap-2"
          data-testid="generate-button"
        >
          {pending ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              {tDetail('generating')}
            </>
          ) : events.length > 0 ? (
            <>
              <Play className="h-4 w-4" aria-hidden="true" />
              {tDetail('regenerate')}
            </>
          ) : (
            <>
              <Play className="h-4 w-4" aria-hidden="true" />
              {tDetail('generate')}
            </>
          )}
        </Button>

        {events.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {pending ? t('connecting') : t('noEventsYet')}
          </p>
        ) : (
          <ol className="space-y-1 text-sm" data-testid="saga-events">
            {events.map((event, idx) => (
              <li key={idx} className="flex items-start gap-2 font-mono text-xs">
                <EventIcon type={event.type} />
                <EventLabel event={event} t={t} />
              </li>
            ))}
          </ol>
        )}
        {closed ? (
          <p className="text-xs text-muted-foreground">{t('closed')}</p>
        ) : null}
      </CardContent>
    </Card>
  );
}

function EventIcon({ type }: { type: string }) {
  if (type === 'completed' || type === 'agent_completed') {
    return <CheckCircle2 className="h-4 w-4 text-emerald-600" aria-hidden="true" />;
  }
  if (type === 'error' || type === 'agent_failed') {
    return <XCircle className="h-4 w-4 text-red-600" aria-hidden="true" />;
  }
  return <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-hidden="true" />;
}

function EventLabel({
  event,
  t,
}: {
  event: SsaEvent;
  t: ReturnType<typeof useTranslations>;
}) {
  const agent = typeof event.payload?.agent === 'string' ? event.payload.agent : '';
  const ms =
    typeof event.payload?.duration_ms === 'number' ? event.payload.duration_ms : 0;
  const errorStr =
    typeof event.payload?.error === 'string' ? event.payload.error : '';
  switch (event.type) {
    case 'saga_started':
      return <span>{t('started')}</span>;
    case 'agent_started':
      return <span>{t('agentStarted', { agent })}</span>;
    case 'agent_completed':
      return <span>{t('agentCompleted', { agent, ms })}</span>;
    case 'agent_failed':
      return <span>{t('agentFailed', { agent })}</span>;
    case 'completed':
      return <span>{t('saga_complete')}</span>;
    case 'error':
      return <span>{t('saga_failed', { error: errorStr })}</span>;
    default:
      return <span>{event.type}</span>;
  }
}
