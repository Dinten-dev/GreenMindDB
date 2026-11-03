import type { Metadata } from 'next';
import { getTranslations } from 'next-intl/server';

export async function generateMetadata({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<Metadata> {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: 'ObservationMetadata' });
  return {
    title: t('title'),
    description: t('description'),
    robots: { index: false, follow: false },
  };
}
export default function ObservationLayout({ children }: { children: React.ReactNode }) {
  return <div className="font-sans min-h-screen bg-gray-50">{children}</div>;
}
