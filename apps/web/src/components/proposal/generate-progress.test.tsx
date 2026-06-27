import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import enMessages from '@/messages/en.json';
import { apiClient } from '@/lib/api/client';

import { GenerateProgress } from './generate-progress';

import type { JobStatusResponse } from '@/lib/api/queries';

vi.mock('@/lib/api/client', () => ({
  apiClient: vi.fn(),
}));

// env.apiUrl is a fail-fast getter over NEXT_PUBLIC_API_URL — stub the
// module so the component can compose stream URLs without real env.
vi.mock('@/lib/env', () => ({
  env: { apiUrl: 'http://api.test' },
}));

const apiClientMock = vi.mocked(apiClient);

/**
 * jsdom has no EventSource. This stub records instances and lets a test
 * fire `onerror` — reproducing production, where the stream 403s
 * (EventSource cannot carry the bearer header) and polling must carry
 * the run to completion on its own.
 */
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  close(): void {
    this.closed = true;
  }
}

const ENQUEUED = {
  job_id: 'job-1',
  proposal_id: 'p-1',
  estimated_duration_seconds: 600,
  status_url: '/api/v1/jobs/job-1',
  stream_url: '/api/v1/proposals/p-1/stream',
};

function jobResponse(
  status: JobStatusResponse['status'],
  error: string | null = null,
): JobStatusResponse {
  return { id: 'job-1', status, result: null, error };
}

function mockApi(jobStatus: JobStatusResponse['status'], error: string | null = null) {
  apiClientMock.mockImplementation((path: string) => {
    if (path.includes('/generate')) return Promise.resolve(ENQUEUED);
    if (path.includes('/jobs/')) return Promise.resolve(jobResponse(jobStatus, error));
    return Promise.reject(new Error(`unexpected path: ${path}`));
  });
}

function renderProgress(onCompleted = vi.fn()) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <NextIntlClientProvider locale="en" messages={enMessages}>
        <GenerateProgress
          proposalId="p-1"
          generating={false}
          onCompleted={onCompleted}
        />
      </NextIntlClientProvider>
    </QueryClientProvider>,
  );
  return onCompleted;
}

beforeEach(() => {
  apiClientMock.mockReset();
  FakeEventSource.instances = [];
  vi.stubGlobal('EventSource', FakeEventSource);
});

describe('<GenerateProgress> job-polling fallback', () => {
  it('completes via polling even when the SSE stream errors (prod path)', async () => {
    mockApi('completed');
    const onCompleted = renderProgress();

    fireEvent.click(screen.getByTestId('generate-button'));

    // SSE opens after the POST resolves, then 403s in production —
    // simulate by firing onerror as soon as the stub exists.
    await waitFor(() => expect(FakeEventSource.instances.length).toBe(1));
    FakeEventSource.instances[0]?.onerror?.();

    // Polling (the authoritative channel) still drives completion.
    await waitFor(() => expect(onCompleted).toHaveBeenCalledTimes(1));
  });

  it('renders the queued status row while waiting for a worker', async () => {
    mockApi('queued');
    renderProgress();

    fireEvent.click(screen.getByTestId('generate-button'));

    await waitFor(() =>
      expect(screen.getByTestId('job-status')).toHaveTextContent(
        'Queued — waiting for a worker…',
      ),
    );
  });

  it('surfaces a failed job with its error and still fires onCompleted', async () => {
    mockApi('failed', 'LLM provider exploded');
    const onCompleted = renderProgress();

    fireEvent.click(screen.getByTestId('generate-button'));

    await waitFor(() =>
      expect(screen.getByTestId('job-failed')).toHaveTextContent(
        'Generation failed: LLM provider exploded',
      ),
    );
    // onCompleted also fires on failure — the parent refetches the
    // proposal and renders its failed state.
    expect(onCompleted).toHaveBeenCalledTimes(1);
  });
});
