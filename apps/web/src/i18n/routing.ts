import { defineRouting } from 'next-intl/routing';

/**
 * Locale routing configuration for next-intl.
 *
 * `as-needed` strategy: the default locale (tr) is served from `/` while
 * `en` lives under `/en/*`. Keeps the canonical URLs short and matches
 * how Bluedev's marketing site routes.
 */
export const routing = defineRouting({
  locales: ['tr', 'en'],
  defaultLocale: 'tr',
  localePrefix: 'as-needed',
});

export type Locale = (typeof routing.locales)[number];
