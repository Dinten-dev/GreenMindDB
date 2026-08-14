import type { Metadata } from 'next';
import { Inter } from 'next/font/google';

const inter = Inter({
    subsets: ['latin'],
    display: 'swap',
    variable: '--font-inter',
});

export const metadata: Metadata = {
    title: 'GreenMind – Pflanzenbewertung',
    description: 'Subjektive Pflanzenbewertung via QR-Code für das GreenMind Forschungssystem.',
    robots: { index: false, follow: false },
};

export default function ObserveLayout({
    children,
}: {
    children: React.ReactNode;
}) {
    return (
        <div className={`${inter.variable} font-sans min-h-screen bg-gray-50`}>
            {children}
        </div>
    );
}
