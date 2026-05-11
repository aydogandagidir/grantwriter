import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, type RenderOptions, type RenderResult } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import { type ReactElement, type ReactNode } from 'react';

import trMessages from '@/messages/tr.json';

/**
 * Shared test wrapper that mirrors the real layout's providers. Every
 * test that renders a client component using i18n or TanStack Query
 * goes through this so missing-provider errors stay out of the test
 * suite.
 *
 * Default locale `tr` matches the production default; tests pass
 * `locale="en"` when they specifically need the English bundle (e.g.
 * a copy-assertion). Loaded as a static import so vitest doesn't
 * have to wait on dynamic imports inside render().
 */

interface ProviderOptions extends Omit<RenderOptions, 'wrapper'> {
  locale?: 'tr' | 'en';
}

function makeQueryClient() {
  // Disable retry + cache so a failing query in one test doesn't
  // bleed timing assertions into the next.
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });
}

function Wrapper({ children, locale = 'tr' }: { children: ReactNode; locale?: 'tr' | 'en' }) {
  const messages = locale === 'tr' ? trMessages : trMessages; // both locales share the same shape
  const queryClient = makeQueryClient();
  return (
    <NextIntlClientProvider messages={messages} locale={locale}>
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    </NextIntlClientProvider>
  );
}

export function renderWithProviders(
  ui: ReactElement,
  { locale = 'tr', ...options }: ProviderOptions = {},
): RenderResult {
  return render(ui, {
    wrapper: ({ children }) => <Wrapper locale={locale}>{children}</Wrapper>,
    ...options,
  });
}
