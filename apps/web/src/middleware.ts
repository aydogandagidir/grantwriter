import createIntlMiddleware from 'next-intl/middleware';
import { NextResponse, type NextRequest } from 'next/server';

import { routing } from '@/i18n/routing';
import { updateSession } from '@/lib/supabase/middleware';

const intlMiddleware = createIntlMiddleware(routing);

const PUBLIC_PATH_PREFIXES = [
  '/login',
  '/signup',
  '/home',       // marketing homepage
  '/pricing',    // marketing pricing
  '/invitations', // public invite preview/accept
];

function isPublic(pathname: string): boolean {
  // Strip leading /en or /tr if present so we can match the bare path.
  const stripped = pathname.replace(/^\/(en|tr)(?=\/|$)/, '');
  const target = stripped || '/';
  if (target === '/') return true;
  return PUBLIC_PATH_PREFIXES.some((prefix) => target === prefix || target.startsWith(`${prefix}/`));
}

/**
 * Composed middleware:
 *
 * 1. Refresh the Supabase auth cookie via the SSR helper.
 * 2. Run next-intl locale routing.
 * 3. If the path is private and we have no user, redirect to /login
 *    preserving the locale prefix.
 */
export async function middleware(request: NextRequest) {
  const { response: sessionResponse, user } = await updateSession(request);

  const intlResponse = intlMiddleware(request);
  // Copy any cookies set by Supabase (refresh tokens) into the i18n response.
  for (const cookie of sessionResponse.cookies.getAll()) {
    intlResponse.cookies.set(cookie.name, cookie.value);
  }

  const { pathname, search } = request.nextUrl;
  if (!user && !isPublic(pathname)) {
    const url = request.nextUrl.clone();
    url.pathname = pathname.startsWith('/en/') || pathname === '/en' ? '/en/login' : '/login';
    url.search = '';
    url.searchParams.set('next', `${pathname}${search}`);
    return NextResponse.redirect(url);
  }

  return intlResponse;
}

export const config = {
  // Match every request EXCEPT static assets + Next internals + the API
  // proxy + sitemap/robots.
  matcher: [
    '/((?!api|_next/static|_next/image|favicon.ico|robots.txt|sitemap.xml|.*\\.(?:png|jpg|jpeg|svg|gif|webp|ico|css|js|woff2?|ttf|otf)$).*)',
  ],
};
