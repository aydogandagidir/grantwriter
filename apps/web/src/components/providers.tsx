'use client';

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';

/**
 * Single source of all client-side providers. Wraps every page so
 * components anywhere in the tree can call `useQuery` etc. without
 * worrying about the provider chain.
 *
 * The QueryClient is built lazily inside useState so it doesn't get
 * recreated on every render (which would invalidate caches).
 */
export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            retry: (failureCount, error: unknown) => {
              const status = (error as { status?: number } | null)?.status ?? 0;
              // Don't retry auth or client errors — they won't change.
              if (status >= 400 && status < 500) return false;
              return failureCount < 2;
            },
            refetchOnWindowFocus: false,
          },
        },
      }),
  );

  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}
