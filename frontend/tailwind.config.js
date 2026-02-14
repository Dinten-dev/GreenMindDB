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
                sans: ['-apple-system', 'BlinkMacSystemFont', 'SF Pro Text', 'SF Pro Display', 'Helvetica Neue', 'Arial', 'sans-serif'],
            },
            colors: {
                // Apple-inspired color palette
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
                'apple-blue': {
                    DEFAULT: '#0071e3',
                    hover: '#0077ed',
                    light: '#147ce5',
                },
                'apple-green': {
                    DEFAULT: '#34c759',
                    light: '#30d158',
                },
            },
            boxShadow: {
                'apple': '0 4px 16px rgba(0, 0, 0, 0.08)',
                'apple-lg': '0 8px 32px rgba(0, 0, 0, 0.12)',
                'apple-card': '0 2px 8px rgba(0, 0, 0, 0.04)',
            },
            borderRadius: {
                'apple': '12px',
                'apple-lg': '18px',
                'apple-xl': '24px',
            },
        },
    },
    plugins: [],
}
