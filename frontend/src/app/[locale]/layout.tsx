import type { Metadata } from 'next';
import '../globals.css';
import GlobalBackground from '@/components/GlobalBackground';
import Navbar from '@/components/Navbar';
import Footer from '@/components/Footer';
import { NextIntlClientProvider } from 'next-intl';
import { getMessages } from 'next-intl/server';

export const metadata: Metadata = {
  title: 'GreenMind — Forschungsplattform für bioelektrische Phytosensorik',
  description:
    'Forschungs- und Entwicklungsplattform der Galaxyadvisors AG für die datenbasierte Analyse bioelektrischer Pflanzensignale.',
  openGraph: {
    title: 'GreenMind — Forschungsplattform',
    description: 'Datenbasierte Analyse bioelektrischer Pflanzensignale in der Schweiz.',
    url: 'https://green-mind.ch',
    siteName: 'GreenMind',
    locale: 'de_CH',
    type: 'website',
  },
  robots: {
    index: true,
    follow: true,
  },
};

const jsonLd = {
  '@context': 'https://schema.org',
  '@type': 'LocalBusiness',
  name: 'Galaxyadvisors AG',
  url: 'https://green-mind.ch',
  address: {
    '@type': 'PostalAddress',
    streetAddress: 'Laurenzenvorstadt 69',
    addressLocality: 'Aarau',
    postalCode: '5000',
    addressCountry: 'CH',
  },
};

export default async function RootLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  const messages = await getMessages();

  return (
    <html lang={locale}>
      <head>
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
        />
      </head>
      <body className="min-h-screen flex flex-col bg-apple-gray-50 text-apple-gray-800">
        <NextIntlClientProvider messages={messages}>
          <GlobalBackground />
          <Navbar />
          <main className="flex-1">{children}</main>
          <Footer />
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
