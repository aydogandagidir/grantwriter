import createNextIntlPlugin from 'next-intl/plugin';

const withNextIntl = createNextIntlPlugin('./src/i18n/request.ts');

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  transpilePackages: ['@bluedev/shared-types'],
  // typedRoutes graduated out of experimental in 15.x; we keep it off for
  // now so the bundler doesn't try to type-check the i18n-prefixed paths
  // (next-intl's <Link> handles them safely).
  typedRoutes: false,
};

export default withNextIntl(nextConfig);
