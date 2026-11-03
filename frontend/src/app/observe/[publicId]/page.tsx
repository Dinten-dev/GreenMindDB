import { redirect } from 'next/navigation';
import { locales } from '@/i18n/locales';

export default async function LegacyObservation({
  params,
  searchParams,
}: {
  params: Promise<{ publicId: string }>;
  searchParams: Promise<{ lang?: string }>;
}) {
  const { publicId } = await params;
  const { lang } = await searchParams;
  const locale = locales.find((value) => value === lang) || 'de';
  redirect(`/${locale}/observe/${encodeURIComponent(publicId)}`);
}
