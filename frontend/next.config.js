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
        ignoreDuringBuilds: true,
    },
}

module.exports = nextConfig
