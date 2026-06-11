import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { apiClient } from '@/lib/api/client';
import { jobPollInterval, useJob } from '@/lib/api/queries';

import type { JobStatusResponse } from '@/lib/api/queries';

vi.mock('@/lib/api/client', () => ({
  apiClient: vi.fn(),
}));

const apiClientMock = vi.mocked(apiClient);

function wrapper({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

function job(status: JobStatusResponse['status']): JobStatusResponse {
  return { id: 'j-1', status, result: null, error: null };
}

beforeEach(() => {
  apiClientMock.mockReset();
});

describe('jobPollInterval', () => {
  // The cadence contract: keep polling through unknown/queued/running,
  // stop dead on terminal states. This pure helper IS the hook's
  // refetchInterval — testing it directly avoids fake-timer flake.
  it.each([
    [undefined, 3000],
    [job('queued'), 3000],
    [job('running'), 3000],
    [job('completed'), false],
    [job('failed'), false],
  ] as const)('maps %j to %j', (data, expected) => {
    expect(jobPollInterval(data as JobStatusResponse | undefined)).toBe(expected);
  });
});

describe('useJob', () => {
  it('stays parked (no fetch) when jobId is null', async () => {
    const { result } = renderHook(() => useJob(null), { wrapper });

    // Give the query a tick to (not) fire.
    await waitFor(() => expect(result.current.fetchStatus).toBe('idle'));
    expect(apiClientMock).not.toHaveBeenCalled();
    expect(result.current.data).toBeUndefined();
  });

  it('fetches the job and exposes the terminal payload', async () => {
    apiClientMock.mockResolvedValue({
      id: 'j-9',
      status: 'completed',
      result: { signed_url: 'https://files.example/out.docx' },
      error: null,
    } satisfies JobStatusResponse);

    const { result } = renderHook(() => useJob('j-9'), { wrapper });

    await waitFor(() => expect(result.current.data?.status).toBe('completed'));
    expect(apiClientMock).toHaveBeenCalledWith('/api/v1/jobs/j-9');
    expect(result.current.data?.result?.signed_url).toBe(
      'https://files.example/out.docx',
    );
  });
});
