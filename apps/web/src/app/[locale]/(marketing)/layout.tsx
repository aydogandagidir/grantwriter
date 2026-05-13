import type { ReactNode } from 'react';

/**
 * Marketing layout — public pages (homepage, pricing) that do NOT require
 * authentication and render outside the app shell (no sidebar/topbar).
 *
 * The `(marketing)` route group keeps these pages in the same [locale]
 * segment so next-intl works transparently.
 */
export default function MarketingLayout({ children }: { children: ReactNode }) {
  return <>{children}</>;
}
