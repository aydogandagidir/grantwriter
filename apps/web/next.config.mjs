/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  transpilePackages: ['@bluedev/shared-types'],
  experimental: {
    typedRoutes: true,
  },
};

export default nextConfig;
