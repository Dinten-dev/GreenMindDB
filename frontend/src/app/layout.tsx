import type { Metadata } from 'next'
import './globals.css'
import ClientLayout from './ClientLayout'

export const metadata: Metadata = {
    title: 'Plant Wiki - Optimal Growing Conditions',
    description: 'A comprehensive database of optimal growing conditions for common garden plants.',
}

export default function RootLayout({
    children,
}: {
    children: React.ReactNode
}) {
    return (
        <html lang="en">
            <body className="min-h-screen bg-white">
                <ClientLayout>{children}</ClientLayout>
            </body>
        </html>
    )
}
