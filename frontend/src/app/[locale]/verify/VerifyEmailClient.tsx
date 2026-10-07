'use client';

import { useLocale, useTranslations } from 'next-intl';

import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';
import { apiVerifyEmail } from '@/lib/api';

type VerificationState = 'invalid' | 'pending' | 'success' | 'error';

export default function VerifyEmailClient({ token }: { token: string }) {
  const copy = useTranslations('Content');
  const locale = useLocale();
  const validToken = /^[a-f0-9]{32}$/.test(token);
  const [state, setState] = useState<VerificationState>(validToken ? 'pending' : 'invalid');
  const [message, setMessage] = useState(
    validToken ? copy('verifyingYourEmailAddress') : copy('theVerificationLinkIsInvalid')
  );
  const requested = useRef(false);

  useEffect(() => {
    // The initial HTTPS request is proxy-redacted; remove the one-time token
    // from the browser address/history before any subsequent navigation.
    if (window.location.search) {
      window.history.replaceState(null, '', window.location.pathname);
    }

    if (!validToken || requested.current) return;
    requested.current = true;

    apiVerifyEmail(token)
      .then(() => {
        setState('success');
        setMessage(copy('verifiedMessage'));
      })
      .catch((error: unknown) => {
        setState('error');
        setMessage(
          error instanceof Error ? error.message : copy('yourEmailAddressCouldNotBeVerified')
        );
      });
  }, [token, validToken, copy]);

  const isSuccess = state === 'success';

  return (
    <main className="min-h-screen flex items-center justify-center px-6 bg-apple-gray-100">
      <section className="w-full max-w-md rounded-apple-lg bg-white p-8 text-center shadow-apple">
        <div
          role="status"
          className={`mx-auto mb-5 flex h-12 w-12 items-center justify-center rounded-full text-xl ${
            isSuccess ? 'bg-green-50 text-green-700' : 'bg-apple-gray-100 text-apple-gray-600'
          }`}
        >
          {isSuccess ? '✓' : state === 'pending' ? '…' : '!'}
        </div>
        <h1 className="text-2xl font-bold text-apple-gray-800">
          {isSuccess ? copy('emailVerified') : copy('emailVerification')}
        </h1>
        <p className="mt-3 text-sm text-apple-gray-500">{message}</p>
        {state !== 'pending' && (
          <Link
            href={`/${locale}/login`}
            className="mt-6 inline-flex rounded-apple bg-gm-green-500 px-5 py-2.5 text-sm font-medium text-white transition-colors hover:bg-gm-green-600"
          >
            {copy('goToSignIn')}
          </Link>
        )}
      </section>
    </main>
  );
}
