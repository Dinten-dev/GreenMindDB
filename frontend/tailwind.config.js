/** @type {import('tailwindcss').Config} */
module.exports = {
    content: [
        './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
        './src/components/**/*.{js,ts,jsx,tsx,mdx}',
        './src/app/**/*.{js,ts,jsx,tsx,mdx}',
    ],
    theme: {
        extend: {
            fontFamily: {
                sans: ['var(--font-inter)', 'Inter', '-apple-system', 'BlinkMacSystemFont', 'SF Pro Text', 'SF Pro Display', 'Helvetica Neue', 'Arial', 'sans-serif'],
            },
            colors: {
                'gm-green': {
                    50: '#f0fdf4',
                    100: '#dcfce7',
                    200: '#bbf7d0',
                    300: '#86efac',
                    400: '#4ade80',
                    500: '#34c759',
                    600: '#248a3d',
                    700: '#15803d',
                    800: '#166534',
                    900: '#14532d',
                },
                'apple-gray': {
                    50: '#fafafa',
                    100: '#f5f5f7',
                    200: '#e8e8ed',
                    300: '#d2d2d7',
                    400: '#86868b',
                    500: '#6e6e73',
                    600: '#515154',
                    700: '#424245',
                    800: '#1d1d1f',
                    900: '#111111',
                },
            },
            boxShadow: {
                'apple': '0 4px 16px rgba(0, 0, 0, 0.06)',
                'apple-lg': '0 8px 32px rgba(0, 0, 0, 0.08)',
                'apple-card': '0 2px 8px rgba(0, 0, 0, 0.04)',
                'apple-hover': '0 4px 20px rgba(0, 0, 0, 0.1)',
            },
            borderRadius: {
                'apple': '12px',
                'apple-lg': '16px',
                'apple-xl': '20px',
            },
            animation: {
                'fade-in': 'fadeIn 0.5s ease forwards',
                'slide-up': 'slideUp 0.5s ease forwards',
            },
            keyframes: {
                fadeIn: {
                    '0%': { opacity: '0' },
                    '100%': { opacity: '1' },
                },
                slideUp: {
                    '0%': { opacity: '0', transform: 'translateY(20px)' },
                    '100%': { opacity: '1', transform: 'translateY(0)' },
                },
            },
        },
    },
    plugins: [],
}
