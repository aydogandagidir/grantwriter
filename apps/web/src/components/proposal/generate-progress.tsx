'use client';

import { useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, Loader2, Play, XCircle } from 'lucide-react';
import { useTranslations } from 'next-intl';
import { useEffect, useRef, useState } from 'react';

import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { env } from '@/lib/env';
import { queryKeys, useGenerateProposal, useJob } from '@/lib/api/queries';

interface SsaEvent {
  type: string;
  payload: Record<string, unknown>;
}

/**
 * Generate + progress component.
 *
 * Progress is driven by TWO channels with different reliability:
 *
 * 1. **Job polling (primary, authoritative).** The generate POST returns
 *    a Celery ``job_id``; `useJob` polls `/api/v1/jobs/{id}` every 3s
 *    through the authenticated `apiClient`. queued/running render as a
 *    status row; completed/failed fire `onCompleted()` so the parent
 *    refetches the proposal and swaps in the draft.
 * 2. **SSE (best-effort enhancement).** The stream endpoint requires a
 *    bearer token the browser's native `EventSource` cannot send, so in
 *    production the stream 403s and `onerror` fires — that is expected
 *    and harmless. When SSE *does* work (e.g. behind a cookie-auth
 *    proxy, or after the Faz-3 token-param fix), its per-agent timeline
 *    rows render on top of the polling baseline.
 *
 * Page-reload-mid-flight: the job id lives only in this component's
 * state, so after a reload we can't poll the job. Instead, while the
 * proposal reports `generating` and we hold no job id, we invalidate
 * the proposal query every 5s — when the saga finishes, `status` flips
 * and the parent re-renders with the draft.
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
  const qc = useQueryClient();
  const [streamUrl, setStreamUrl] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [events, setEvents] = useState<SsaEvent[]>([]);
  const [closed, setClosed] = useState(false);

  const job = useJob(jobId);
  const jobStatus = job.data?.status ?? null;

  // Fire onCompleted exactly once per run, whichever channel reports
  // terminal first (SSE completed event vs. polled completed/failed).
  const completionFiredRef = useRef(false);
  const fireCompletion = () => {
    if (completionFiredRef.current) return;
    completionFiredRef.current = true;
    setClosed(true);
    onCompleted();
  };
  // Stable ref so the polling effect below doesn't re-subscribe on every
  // render (fireCompletion closes over state setters that never change).
  const fireCompletionRef = useRef(fireCompletion);
  fireCompletionRef.current = fireCompletion;

  // Channel 1 (primary): polled job reached a terminal state.
  useEffect(() => {
    if (jobStatus === 'completed' || jobStatus === 'failed') {
      fireCompletionRef.current();
    }
  }, [jobStatus]);

  // Reload-mid-flight: generating but no job id to poll → nudge the
  // proposal query until the saga finishes server-side.
  useEffect(() => {
    if (!generating || jobId !== null || closed) {
      return;
    }
    const timer = setInterval(() => {
      void qc.invalidateQueries({ queryKey: queryKeys.proposal(proposalId) });
    }, 5000);
    return () => clearInterval(timer);
  }, [generating, jobId, closed, proposalId, qc]);

  // Channel 2 (best-effort): open SSE when we get a stream URL (after
  // the POST returns) OR when the proposal already shows
  // `status=generating` (reload mid-flight).
  useEffect(() => {
    if (!streamUrl && generating) {
      setStreamUrl(`${env.apiUrl}/api/v1/proposals/${proposalId}/stream`);
    }
  }, [streamUrl, generating, proposalId]);

  useEffect(() => {
    if (!streamUrl) {
      return;
    }
    const source = new EventSource(streamUrl, { withCredentials: true });

    source.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as SsaEvent;
        setEvents((prev) => [...prev, data]);
        if (data.type === 'completed' || data.type === 'error') {
          source.close();
          fireCompletionRef.current();
        }
      } catch {
        // Malformed event — ignore; SSE retains the connection.
      }
    };

    source.onerror = () => {
      // Expected in production (EventSource cannot send the bearer
      // header → 403). Close quietly; job polling stays authoritative,
      // so we deliberately do NOT mark the run as closed here.
      source.close();
    };

    return () => {
      source.close();
    };
  }, [streamUrl]);

  const onClickGenerate = async () => {
    const result = await generate.mutateAsync();
    completionFiredRef.current = false;
    setJobId(result.job_id);
    setStreamUrl(`${env.apiUrl}${result.stream_url}`);
    setEvents([]);
    setClosed(false);
  };

  const jobActive = jobStatus === 'queued' || jobStatus === 'running';
  const pending = generate.isPending || jobActive || (generating && !closed);

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
          jobStatus === 'failed' ? (
            <p className="text-sm text-destructive" data-testid="job-failed">
              {t('jobFailed', { error: job.data?.error ?? 'unknown' })}
            </p>
          ) : (
            <p className="text-sm text-muted-foreground" data-testid="job-status">
              {jobStatus === 'queued'
                ? t('jobQueued')
                : jobStatus === 'running'
                  ? t('jobRunning')
                  : pending
                    ? t('connecting')
                    : t('noEventsYet')}
            </p>
          )
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
