import type { Metadata } from 'next';
import '../globals.css';
import GlobalBackground from '@/components/GlobalBackground';
import Navbar from '@/components/Navbar';
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
      title: t('ogTitle'),
      description: t('ogDescription'),
      url: 'https://green-mind.ch',
      siteName: 'GreenMind',
      locale: `${locale}_CH`,
      type: 'website',
    },
    robots: {
      index: true,
      follow: true,
    },
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
        <NextIntlClientProvider locale={locale} messages={messages}>
          <GlobalBackground />
          <Navbar />
          <main className="flex-1">{children}</main>
          <Footer />
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
