'use client';

import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';
import { apiVerifyEmail } from '@/lib/api';

type VerificationState = 'invalid' | 'pending' | 'success' | 'error';

export default function VerifyEmailClient({ token }: { token: string }) {
  const validToken = /^[a-f0-9]{32}$/.test(token);
  const [state, setState] = useState<VerificationState>(validToken ? 'pending' : 'invalid');
  const [message, setMessage] = useState(
    validToken ? 'Ihre E-Mail-Adresse wird bestätigt…' : 'Der Bestätigungslink ist ungültig.'
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
      .then((response) => {
        setState('success');
        setMessage(response.detail);
      })
      .catch((error: unknown) => {
        setState('error');
        setMessage(
          error instanceof Error
            ? error.message
            : 'Die E-Mail-Adresse konnte nicht bestätigt werden.'
        );
      });
  }, [token, validToken]);

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
          {isSuccess ? 'E-Mail bestätigt' : 'E-Mail-Bestätigung'}
        </h1>
        <p className="mt-3 text-sm text-apple-gray-500">{message}</p>
        {state !== 'pending' && (
          <Link
            href="/login"
            className="mt-6 inline-flex rounded-apple bg-gm-green-500 px-5 py-2.5 text-sm font-medium text-white transition-colors hover:bg-gm-green-600"
          >
            Zur Anmeldung
          </Link>
        )}
      </section>
    </main>
  );
}
