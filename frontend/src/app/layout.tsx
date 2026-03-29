import type { Metadata } from 'next'
import { Inter } from 'next/font/google'
import './globals.css'
import GlobalBackground from '@/components/GlobalBackground'
import Navbar from '@/components/Navbar'

const inter = Inter({
    subsets: ['latin'],
    display: 'swap',
    variable: '--font-inter',
})

export const metadata: Metadata = {
    title: 'GreenMind — Forschungsplattform für bioelektrische Phytosensorik',
    description: 'Forschungs- und Entwicklungsplattform der Galaxyadvisors AG für die datenbasierte Analyse bioelektrischer Pflanzensignale.',
}

export default function RootLayout({
    children,
}: {
    children: React.ReactNode
}) {
    return (
        <html lang="de" className={inter.variable}>
            <body className={`${inter.className} min-h-screen bg-apple-gray-50 text-apple-gray-800`}>
                <GlobalBackground />
                <Navbar />
                {children}
            </body>
        </html>
    )
}

