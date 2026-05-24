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

type SearchParamScalar = string | number | boolean;
type SearchParamValue = SearchParamScalar | SearchParamScalar[] | null | undefined;

export interface ApiRequestInit extends Omit<RequestInit, 'body'> {
  body?: unknown;
  searchParams?: Record<string, SearchParamValue>;
  /**
   * Max retry attempts for gateway/cold-start failures (502/503/504 and
   * network errors). Defaults to {@link DEFAULT_RETRIES}. Set to 0 to
   * disable — useful for genuinely non-idempotent calls where a duplicate
   * side effect would be worse than surfacing the error.
   */
  retries?: number;
}

/**
 * Statuses safe to retry: a gateway/proxy returned them because it could
 * not reach a running app instance, so the handler never executed — which
 * makes retrying a write safe too.
 *
 * 503 is deliberately EXCLUDED. This backend raises 503 as an
 * application-level "capability not configured" signal (missing Supabase
 * JWT config, unset Iyzico/LLM keys, etc.) that will never resolve on
 * retry. Retrying it would waste the budget and, worse, mask an
 * actionable ``detail`` message behind a generic "try again".
 */
const RETRYABLE_STATUSES = new Set([502, 504]);
const DEFAULT_RETRIES = 3;
const RETRY_BASE_DELAY_MS = 400;

function backoffDelay(attempt: number): number {
  // Exponential (400ms, 800ms, 1600ms…) with jitter to avoid a thundering
  // herd when several tabs wake a cold backend at once.
  const base = RETRY_BASE_DELAY_MS * 2 ** attempt;
  return base + Math.random() * RETRY_BASE_DELAY_MS;
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

export function buildUrl(path: string, searchParams?: ApiRequestInit['searchParams']): string {
  const base = env.apiUrl.replace(/\/$/, '');
  const cleaned = path.startsWith('/') ? path : `/${path}`;
  const url = new URL(`${base}${cleaned}`);
  if (searchParams) {
    for (const [key, value] of Object.entries(searchParams)) {
      if (value === undefined || value === null) continue;
      if (Array.isArray(value)) {
        // FastAPI's ``Query(default=None)`` for ``list[str]`` reads each
        // value as a separate occurrence of the same key — `?k=a&k=b`
        // rather than `?k=a,b`. ``append`` matches that contract.
        for (const item of value) {
          if (item === undefined || item === null) continue;
          url.searchParams.append(key, String(item));
        }
      } else {
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

  const url = buildUrl(path, init.searchParams);
  const maxRetries = init.retries ?? DEFAULT_RETRIES;

  for (let attempt = 0; ; attempt++) {
    let response: Response;
    try {
      // `searchParams`/`retries` are non-RequestInit extras; fetch ignores
      // unknown init keys, so spreading the whole object is harmless.
      response = await fetch(url, { ...init, headers, body: bodyToSend });
    } catch (err) {
      // Network-level failure (DNS, connection refused, cold-start TCP
      // reset). Retry within budget; never retry a caller-driven abort.
      const aborted = err instanceof DOMException && err.name === 'AbortError';
      if (aborted || attempt >= maxRetries) throw err;
      await sleep(backoffDelay(attempt));
      continue;
    }

    if (!response.ok) {
      if (RETRYABLE_STATUSES.has(response.status) && attempt < maxRetries) {
        await sleep(backoffDelay(attempt));
        continue;
      }
      throw await parseError(response);
    }
    if (response.status === 204) {
      return undefined as T;
    }
    return (await response.json()) as T;
  }
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
