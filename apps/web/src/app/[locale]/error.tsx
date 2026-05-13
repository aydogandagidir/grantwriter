'use client';

import { useEffect } from 'react';

import { Button } from '@/components/ui/button';
import { captureException } from '@/lib/sentry';

/**
 * Next.js global error boundary. Catches unhandled errors in the
 * React tree and:
 * 1. Reports them to Sentry (if configured)
 * 2. Shows a user-friendly fallback UI
 *
 * This file is a Next.js convention — placed at `app/[locale]/error.tsx`
 * it catches errors in any page under the locale layout.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    captureException(error);
  }, [error]);

  return (
    <div className="flex min-h-[50vh] flex-col items-center justify-center gap-4 px-4 text-center">
      <div className="flex h-16 w-16 items-center justify-center rounded-full bg-destructive/10">
        <span className="text-2xl">⚠️</span>
      </div>
      <h2 className="text-xl font-semibold">Bir şeyler ters gitti</h2>
      <p className="max-w-md text-sm text-muted-foreground">
        Beklenmeyen bir hata oluştu. Lütfen tekrar deneyin veya sorun devam
        ederse destek ekibiyle iletişime geçin.
      </p>
      {error.digest && (
        <p className="text-xs text-muted-foreground">
          Referans: <code className="rounded bg-muted px-1.5 py-0.5">{error.digest}</code>
        </p>
      )}
      <Button onClick={reset} variant="outline">
        Tekrar dene
      </Button>
    </div>
  );
}
