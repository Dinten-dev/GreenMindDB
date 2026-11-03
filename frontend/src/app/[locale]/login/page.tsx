'use client';

import BrandLogo from '@/components/BrandLogo';
import { Suspense, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useLocale, useTranslations } from 'next-intl';
import { useAuth } from '@/contexts/AuthContext';
import { loginErrorKey } from '@/lib/auth-errors';
import { localePath, safeReturnTo } from '@/lib/auth-routing';

function LoginForm() {
  const router = useRouter();
  const query = useSearchParams();
  const locale = useLocale();
  const t = useTranslations('Auth');
  const { user, status, issue, login, refresh } = useAuth();
  const next = safeReturnTo(locale, query.get('next'));
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [visible, setVisible] = useState(false);
  const submitting = useRef(false);

  useEffect(() => {
    if (user && status === 'authenticated') router.replace(next);
  }, [user, status, router, next]);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (submitting.current) return;
    submitting.current = true;
    setError('');
    setLoading(true);
    try {
      await login(email, password);
    } catch (error: unknown) {
      setError(loginErrorKey(error));
    } finally {
      submitting.current = false;
      setLoading(false);
    }
  };
  const errorKey = error || (issue && issue !== 'expired' ? issue : '');
  const expired = !errorKey && (issue === 'expired' || query.get('reason') === 'expired');

  return (
    <div className="min-h-screen flex items-center justify-center px-6 py-24 bg-apple-gray-100">
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <Link
            href={`/${locale}`}
            className="inline-flex items-center gap-2 mb-6"
            aria-label="GreenMind"
          >
            <BrandLogo size={40} />
          </Link>
          <h1 className="text-2xl font-bold text-apple-gray-800">{t('title')}</h1>
          <p className="text-sm text-apple-gray-400 mt-2">{t('intro')}</p>
        </div>
        <div className="bg-white rounded-apple-lg shadow-apple p-8">
          <form onSubmit={handleSubmit} className="space-y-4" aria-busy={loading}>
            {errorKey && (
              <div role="alert" className="px-4 py-3 rounded-apple bg-red-50 text-red-700 text-sm">
                {t(`errors.${errorKey}`)}
              </div>
            )}
            {expired && (
              <p role="status" className="text-sm text-apple-gray-600">
                {t('errors.expired')}
              </p>
            )}
            {status === 'unavailable' && (
              <button
                type="button"
                onClick={() => void refresh()}
                className="text-sm text-gm-green-700 underline"
              >
                {t('retry')}
              </button>
            )}
            <div>
              <label
                htmlFor="login-email"
                className="block text-sm font-medium text-apple-gray-600 mb-1.5"
              >
                {t('email')}
              </label>
              <input
                id="login-email"
                name="email"
                autoComplete="username"
                type="email"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                disabled={loading}
                className="w-full px-4 py-2.5 rounded-apple bg-apple-gray-100 border border-apple-gray-200 text-apple-gray-800 focus:outline-none focus:ring-2 focus:ring-gm-green-500 text-sm"
              />
            </div>
            <div>
              <label
                htmlFor="login-password"
                className="block text-sm font-medium text-apple-gray-600 mb-1.5"
              >
                {t('password')}
              </label>
              <input
                id="login-password"
                name="password"
                autoComplete="current-password"
                type={visible ? 'text' : 'password'}
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                disabled={loading}
                className="w-full px-4 py-2.5 rounded-apple bg-apple-gray-100 border border-apple-gray-200 text-apple-gray-800 focus:outline-none focus:ring-2 focus:ring-gm-green-500 text-sm"
              />
              <button
                type="button"
                onClick={() => setVisible(!visible)}
                aria-pressed={visible}
                aria-controls="login-password"
                className="mt-2 text-xs text-gm-green-700 underline"
              >
                {t(visible ? 'hidePassword' : 'showPassword')}
              </button>
            </div>
            <button
              type="submit"
              disabled={loading}
              className="w-full py-2.5 bg-gm-green-500 text-white rounded-apple font-medium hover:bg-gm-green-600 disabled:opacity-50 disabled:cursor-not-allowed text-sm"
            >
              {t(loading ? 'signingIn' : 'signIn')}
            </button>
          </form>
        </div>
        <p className="text-center text-sm text-apple-gray-400 mt-6">
          {t('noAccount')}{' '}
          <Link
            href={localePath(locale, '/early-access')}
            className="text-gm-green-600 font-medium"
          >
            {t('requestAccess')}
          </Link>
        </p>
      </div>
    </div>
  );
}

export default function LoginPage() {
  const t = useTranslations('Auth');
  return (
    <Suspense
      fallback={
        <p className="p-8" role="status">
          {t('checking')}
        </p>
      }
    >
      <LoginForm />
    </Suspense>
  );
}
