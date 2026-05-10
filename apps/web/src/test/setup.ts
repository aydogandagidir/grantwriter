import '@testing-library/jest-dom/vitest';

// jsdom doesn't ship the matchMedia primitive that some shadcn components
// (Toast, Tooltip side detection) read on mount. Stub it so render() doesn't
// blow up before the test even gets to assert anything.
if (typeof window !== 'undefined' && typeof window.matchMedia !== 'function') {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {
        /* legacy noop */
      },
      removeListener: () => {
        /* legacy noop */
      },
      addEventListener: () => {
        /* noop */
      },
      removeEventListener: () => {
        /* noop */
      },
      dispatchEvent: () => false,
    }),
  });
}

// next-intl's <Link> reads from process.env at import time; provide a
// minimal API URL so module-load doesn't throw inside the API client tests.
process.env.NEXT_PUBLIC_API_URL ??= 'http://api.test';
process.env.NEXT_PUBLIC_SUPABASE_URL ??= 'http://supabase.test';
process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ??= 'anon-key';
