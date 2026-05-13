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

/**
 * One entry of a FastAPI / Pydantic ``422 Unprocessable Entity`` error
 * body. Shape: ``{loc: ["body", "slug"], msg: "...", type: "..."}``.
 *
 * The ``loc`` tuple usually starts with the request source ("body" /
 * "query" / "path") followed by the field path. Consumers typically
 * skip the source prefix when surfacing per-field errors.
 */
export interface ValidationError {
  loc: (string | number)[];
  msg: string;
  type: string;
  input?: unknown;
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
    public readonly body?: unknown,
    /** Populated only on 422 responses with a Pydantic ``detail`` array. */
    public readonly validationErrors?: ValidationError[],
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

function isValidationError(x: unknown): x is ValidationError {
  if (!x || typeof x !== 'object') return false;
  const obj = x as Record<string, unknown>;
  return Array.isArray(obj.loc) && typeof obj.msg === 'string' && typeof obj.type === 'string';
}

/**
 * Render a Pydantic detail payload (string OR array of validation
 * errors) into a single human-readable line. When the raw value is a
 * validation array, the corresponding ``ValidationError[]`` is returned
 * separately so form components can hook per-field errors into RHF
 * ``setError`` without having to re-parse the message.
 */
function formatDetail(raw: unknown): {
  detail: string;
  validationErrors?: ValidationError[];
} {
  if (typeof raw === 'string') return { detail: raw };
  if (Array.isArray(raw)) {
    const errors = raw.filter(isValidationError);
    if (errors.length > 0) {
      const summary = errors
        .map((e) => {
          const field = e.loc.slice(1).join('.') || e.loc.join('.');
          return field ? `${field}: ${e.msg}` : e.msg;
        })
        .join('; ');
      return { detail: summary, validationErrors: errors };
    }
  }
  // Unknown shape — stringify so callers still get *something*, but
  // avoid the classic "[object Object]" by JSON-encoding.
  try {
    return { detail: JSON.stringify(raw) };
  } catch {
    return { detail: String(raw) };
  }
}

export async function parseError(response: Response): Promise<ApiError> {
  let detail = response.statusText;
  let validationErrors: ValidationError[] | undefined;
  let body: unknown;
  try {
    body = await response.json();
    if (body && typeof body === 'object' && 'detail' in body) {
      const formatted = formatDetail((body as { detail: unknown }).detail);
      detail = formatted.detail;
      validationErrors = formatted.validationErrors;
    }
  } catch {
    /* non-JSON body — keep statusText */
  }
  return new ApiError(response.status, detail, body, validationErrors);
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
