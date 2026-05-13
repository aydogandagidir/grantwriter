/**
 * Tests for the typed fetch wrapper. The Supabase + browser bits are
 * import-mocked so the test stays pure-Node — the only thing we want
 * to assert here is the URL composition, body serialisation, and
 * ApiError handling.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from './client';

// Mock the Supabase client BEFORE importing apiClient so the dynamic
// import inside apiClient resolves to our stub.
vi.mock('@/lib/supabase/client', () => ({
  createClient: () => ({
    auth: {
      getSession: async () => ({ data: { session: { access_token: 'test-jwt' } } }),
    },
  }),
}));

import { apiClient } from './client';

describe('apiClient', () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    globalThis.fetch = vi.fn();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  function mockFetchResponse(status: number, body: unknown) {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: status >= 200 && status < 300,
      status,
      statusText: 'mock',
      json: async () => body,
    });
  }

  it('attaches Bearer auth and JSON body, returns parsed JSON', async () => {
    mockFetchResponse(200, { hello: 'world' });
    const result = await apiClient<{ hello: string }>('/api/v1/echo', {
      method: 'POST',
      body: { ping: true },
    });
    expect(result).toEqual({ hello: 'world' });

    const [url, init] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [
      string,
      RequestInit,
    ];
    expect(url).toBe('http://api.test/api/v1/echo');
    const headers = new Headers(init.headers);
    expect(headers.get('Authorization')).toBe('Bearer test-jwt');
    expect(headers.get('Content-Type')).toBe('application/json');
    expect(init.body).toBe(JSON.stringify({ ping: true }));
  });

  it('serialises searchParams, skipping undefined values', async () => {
    mockFetchResponse(200, {});
    await apiClient('/api/v1/things', {
      searchParams: { limit: 10, before: undefined, q: 'hello world' },
    });
    const [url] = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0] as [string];
    expect(url).toBe('http://api.test/api/v1/things?limit=10&q=hello+world');
  });

  it('returns undefined on 204 (No Content)', async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      status: 204,
      statusText: 'No Content',
      json: async () => {
        throw new Error('should not parse');
      },
    });
    const result = await apiClient('/api/v1/delete', { method: 'DELETE' });
    expect(result).toBeUndefined();
  });

  it('throws ApiError with parsed detail on non-2xx', async () => {
    mockFetchResponse(403, { detail: 'forbidden' });
    await expect(apiClient('/api/v1/secret')).rejects.toMatchObject(
      new ApiError(403, 'forbidden'),
    );
  });

  it('throws ApiError with statusText when body is not JSON', async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      status: 500,
      statusText: 'Internal Server Error',
      json: async () => {
        throw new Error('not json');
      },
    });
    await expect(apiClient('/api/v1/oops')).rejects.toMatchObject({
      status: 500,
      detail: 'Internal Server Error',
    });
  });

  it('formats a Pydantic 422 validation array into a human-readable detail', async () => {
    // The "[object Object]" disaster: backend returns
    // {detail: [{loc, msg, type}, ...]}; the old parser ran String(array)
    // which collapsed every entry to "[object Object]" and broke the UI.
    mockFetchResponse(422, {
      detail: [
        {
          type: 'string_too_short',
          loc: ['body', 'name'],
          msg: 'String should have at least 2 characters',
          input: 'x',
        },
        {
          type: 'value_error',
          loc: ['body', 'slug'],
          msg: 'Slug must be 3-50 characters, lowercase...',
        },
      ],
    });

    try {
      await apiClient('/api/v1/onboarding', { method: 'POST', body: {} });
      throw new Error('expected apiClient to throw');
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      const apiErr = err as ApiError;
      expect(apiErr.status).toBe(422);
      expect(apiErr.detail).toBe(
        'name: String should have at least 2 characters; ' +
          'slug: Slug must be 3-50 characters, lowercase...',
      );
      expect(apiErr.validationErrors).toHaveLength(2);
      const firstError = apiErr.validationErrors?.[0];
      expect(firstError).toBeDefined();
      expect(firstError!.loc).toEqual(['body', 'name']);
    }
  });

  it('falls back to JSON-stringified detail for unknown shapes (not "[object Object]")', async () => {
    // Backends sometimes return weird detail objects (e.g. middleware
    // errors). Stringify rather than the default String() that yields
    // "[object Object]".
    mockFetchResponse(500, { detail: { error: 'something_weird', code: 42 } });

    try {
      await apiClient('/api/v1/oops');
      throw new Error('expected apiClient to throw');
    } catch (err) {
      const apiErr = err as ApiError;
      expect(apiErr.detail).toBe('{"error":"something_weird","code":42}');
      expect(apiErr.validationErrors).toBeUndefined();
    }
  });
});
