/**
 * Browser-side typed fetch wrapper.
 *
 * Server-side callers (Route Handlers / Server Components / Server
 * Actions) use the sibling `api/server.ts` instead — the two modules
 * MUST stay split so client bundles never reach `next/headers`.
 *
 * Both throw `ApiError` on non-2xx responses with the parsed JSON
 * detail when available; let TanStack Query surface the error.
 */

import { env } from '@/lib/env';

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
    public readonly body?: unknown,
  ) {
    super(`API ${status}: ${detail}`);
    this.name = 'ApiError';
  }
}

export interface ApiRequestInit extends Omit<RequestInit, 'body'> {
  body?: unknown;
  searchParams?: Record<string, string | number | boolean | undefined>;
}

export function buildUrl(path: string, searchParams?: ApiRequestInit['searchParams']): string {
  const base = env.apiUrl.replace(/\/$/, '');
  const cleaned = path.startsWith('/') ? path : `/${path}`;
  const url = new URL(`${base}${cleaned}`);
  if (searchParams) {
    for (const [key, value] of Object.entries(searchParams)) {
      if (value !== undefined && value !== null) {
        url.searchParams.set(key, String(value));
      }
    }
  }
  return url.toString();
}

export async function parseError(response: Response): Promise<ApiError> {
  let detail = response.statusText;
  let body: unknown;
  try {
    body = await response.json();
    if (body && typeof body === 'object' && 'detail' in body) {
      detail = String((body as { detail: unknown }).detail);
    }
  } catch {
    /* non-JSON body — keep statusText */
  }
  return new ApiError(response.status, detail, body);
}

export async function call<T>(
  path: string,
  init: ApiRequestInit,
  token: string | null,
): Promise<T> {
  const headers = new Headers(init.headers);
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  let bodyToSend: BodyInit | undefined;
  if (init.body !== undefined) {
    if (typeof init.body === 'string' || init.body instanceof FormData) {
      bodyToSend = init.body;
    } else {
      headers.set('Content-Type', 'application/json');
      bodyToSend = JSON.stringify(init.body);
    }
  }

  const response = await fetch(buildUrl(path, init.searchParams), {
    ...init,
    headers,
    body: bodyToSend,
  });

  if (!response.ok) {
    throw await parseError(response);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

/**
 * Browser-side API call. Imports the Supabase browser client lazily so
 * this module stays import-safe in any client component.
 */
export async function apiClient<T>(path: string, init: ApiRequestInit = {}): Promise<T> {
  const { createClient } = await import('@/lib/supabase/client');
  const supabase = createClient();
  const { data } = await supabase.auth.getSession();
  return call<T>(path, init, data.session?.access_token ?? null);
}
