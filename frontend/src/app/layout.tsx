import type { Metadata } from 'next'
import './globals.css'
import GlobalBackground from '@/components/GlobalBackground'
import Navbar from '@/components/Navbar'

export const metadata: Metadata = {
    title: 'GreenMind – Predictive Yield Optimization',
    description: 'Plant bioelectrical sensing system for predictive yield optimization in greenhouse agriculture.',
}

export default function RootLayout({
    children,
}: {
    children: React.ReactNode
}) {
    return (
        <html lang="en">
            <body className="min-h-screen bg-apple-gray-50 text-apple-gray-800">
                <GlobalBackground />
                <Navbar />
                {children}
            </body>
        </html>
    )
}
