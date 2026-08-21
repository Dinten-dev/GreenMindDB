import VerifyEmailClient from './VerifyEmailClient';

interface VerifyPageProps {
  searchParams: Promise<{ token?: string | string[] }>;
}

export default async function VerifyPage({ searchParams }: VerifyPageProps) {
  const params = await searchParams;
  const token = typeof params.token === 'string' ? params.token : '';

  return <VerifyEmailClient token={token} />;
}
