const createNextIntlPlugin = require('next-intl/plugin');

const withNextIntl = createNextIntlPlugin();

/** @type {import('next').NextConfig} */
const nextConfig = {
    output: 'standalone',
    reactStrictMode: true,
    async rewrites() {
        return [
            {
                source: '/api/:path*',
                destination: `${process.env.INTERNAL_API_URL || 'http://localhost:8000'}/api/:path*`,
            },
        ];
    },
    eslint: {
        ignoreDuringBuilds: false,
    },
    typescript: {
        ignoreBuildErrors: false,
    },
}

module.exports = withNextIntl(nextConfig);
