import type { Metadata } from 'next'
import './globals.css'
import ClientLayout from './ClientLayout'

export const metadata: Metadata = {
    title: 'GreenMindDB – Mac mini Dashboard',
    description: 'Monitoring dashboard for the GreenMindDB plant sensor data pipeline. Shows ESP32 devices, ingestion status, and data flows.',
}

export default function RootLayout({
    children,
}: {
    children: React.ReactNode
}) {
    return (
        <html lang="en">
            <body className="min-h-screen bg-gray-50">
                <ClientLayout>{children}</ClientLayout>
            </body>
        </html>
    )
}
