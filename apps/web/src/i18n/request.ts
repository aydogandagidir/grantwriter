import { getRequestConfig } from 'next-intl/server';

import { routing } from './routing';

/**
 * Loads the message bundle for the active locale. Called per request by
 * next-intl's server-side machinery — the dynamic import keeps each
 * locale's JSON out of the bundle until it's actually used.
 */
export default getRequestConfig(async ({ requestLocale }) => {
  const requested = await requestLocale;
  const isSupported =
    typeof requested === 'string' &&
    (routing.locales as readonly string[]).includes(requested);
  const locale = isSupported ? requested : routing.defaultLocale;
  return {
    locale,
    messages: (await import(`../messages/${locale}.json`)).default,
    // A fixed timeZone + now is REQUIRED to avoid hydration mismatches.
    // Without `timeZone`, next-intl formats `dateTime` with the runtime's
    // local zone — UTC on the server, the visitor's zone in the browser —
    // so the same value renders differently on each side. Likewise
    // `relativeTime` needs a shared `now`, otherwise server render time vs.
    // client hydration time diverge. A mismatch corrupts React's fiber
    // tree and surfaces as a commit-phase "insertBefore" NotFoundError.
    // Bluedev is a Türkiye-based product, so we anchor display to İstanbul.
    timeZone: 'Europe/Istanbul',
    now: new Date(),
  };
});
