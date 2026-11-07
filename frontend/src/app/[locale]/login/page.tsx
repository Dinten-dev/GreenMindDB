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
    <div className="min-h-screen flex items-center justify-center px-6 pt-32 pb-24 bg-mercury-bg text-mercury-ink">
      <div className="w-full max-w-[420px]">
        <div className="flex flex-col items-center text-center gap-3 mb-7">
          <Link
            href={`/${locale}`}
            className="inline-flex items-center mb-2"
            aria-label="GreenMind"
          >
            <BrandLogo size={40} />
          </Link>
          <h1 className="landing-display m-0 text-[44px] leading-[1.05]">{t('title')}</h1>
          <p className="m-0 text-base text-mercury-muted">{t('intro')}</p>
        </div>
        <div className="bg-white rounded-[24px] p-8">
          <form onSubmit={handleSubmit} className="space-y-[18px]" aria-busy={loading}>
            {errorKey && (
              <div role="alert" className="px-4 py-3 rounded-[14px] bg-red-50 text-red-800 text-sm">
                {t(`errors.${errorKey}`)}
              </div>
            )}
            {expired && (
              <p role="status" className="text-sm text-mercury-muted">
                {t('errors.expired')}
              </p>
            )}
            {status === 'unavailable' && (
              <button
                type="button"
                onClick={() => void refresh()}
                className="text-sm text-mercury-accent underline"
              >
                {t('retry')}
              </button>
            )}
            <div>
              <label
                htmlFor="login-email"
                className="block text-sm font-medium text-mercury-ink mb-2"
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
                className="w-full h-12 px-4 rounded-[14px] bg-mercury-bg border border-mercury-field text-mercury-ink focus:outline-none focus:ring-2 focus:ring-mercury-accent focus:ring-offset-2 text-[15px]"
              />
            </div>
            <div>
              <label
                htmlFor="login-password"
                className="block text-sm font-medium text-mercury-ink mb-2"
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
                className="w-full h-12 px-4 rounded-[14px] bg-mercury-bg border border-mercury-field text-mercury-ink focus:outline-none focus:ring-2 focus:ring-mercury-accent focus:ring-offset-2 text-[15px]"
              />
              <button
                type="button"
                onClick={() => setVisible(!visible)}
                aria-pressed={visible}
                aria-controls="login-password"
                className="mt-2 text-xs text-mercury-accent underline"
              >
                {t(visible ? 'hidePassword' : 'showPassword')}
              </button>
            </div>
            <button
              type="submit"
              disabled={loading}
              className="w-full mt-1.5 py-[15px] bg-mercury-accent text-white rounded-full font-medium hover:bg-mercury-accent-dark disabled:opacity-50 disabled:cursor-not-allowed text-[15px]"
            >
              {t(loading ? 'signingIn' : 'signIn')}
            </button>
          </form>
        </div>
        <p className="text-center text-sm text-mercury-muted mt-7">
          {t('noAccount')}{' '}
          <Link
            href={localePath(locale, '/early-access')}
            className="text-mercury-accent font-medium hover:text-mercury-accent-dark"
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
