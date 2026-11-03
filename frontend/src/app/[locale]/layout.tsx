import type { Metadata } from 'next';
import '../globals.css';
import GlobalBackground from '@/components/GlobalBackground';
import Navbar from '@/components/Navbar';
import { AuthProvider } from '@/contexts/AuthContext';
import Footer from '@/components/Footer';
import { NextIntlClientProvider } from 'next-intl';
import { getMessages, getTranslations } from 'next-intl/server';

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: 'Metadata' });
  return {
    title: t('title'),
    description: t('description'),
    openGraph: {
      title: t('shortTitle'),
      description: t('shortDescription'),
      url: 'https://green-mind.ch',
      siteName: 'GreenMind',
      locale: ({ de: 'de_CH', en: 'en_GB', fr: 'fr_CH', it: 'it_CH' } as Record<string, string>)[
        locale
      ],
      type: 'website',
    },
    robots: { index: true, follow: true },
  };
}

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
          <AuthProvider>
            <GlobalBackground />
            <Navbar />
            <main className="flex-1">{children}</main>
            <Footer />
          </AuthProvider>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
