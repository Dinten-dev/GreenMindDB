'use client';
import { useState } from 'react';
import Link from 'next/link';
import { useLocale, useTranslations } from 'next-intl';
import { loginErrorKey } from '@/lib/auth-errors';
import Modal from './Modal';
import { useAuth } from '@/contexts/AuthContext';
interface AuthModalProps {
  isOpen: boolean;
  onClose: () => void;
}
export default function AuthModal({ isOpen, onClose }: AuthModalProps) {
  const { login } = useAuth();
  const locale = useLocale();
  const t = useTranslations('Auth');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await login(email, password);
      setPassword('');
      onClose();
    } catch (err) {
      setError(t(`errors.${loginErrorKey(err)}`));
    } finally {
      setLoading(false);
    }
  }
  return (
    <Modal isOpen={isOpen} onClose={onClose} title={t('title')}>
      <form onSubmit={handleSubmit} className="space-y-4">
        {error && (
          <p role="alert" className="text-red-600">
            {error}
          </p>
        )}
        <label className="block">
          {t('email')}
          <input
            type="email"
            required
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full px-3 py-2 border rounded-lg"
          />
        </label>
        <label className="block">
          {t('password')}
          <input
            type="password"
            required
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full px-3 py-2 border rounded-lg"
          />
        </label>
        <button
          type="submit"
          disabled={loading || !email || !password}
          className="w-full py-2 bg-gray-800 text-white rounded-lg disabled:opacity-50"
        >
          {loading ? t('submitting') : t('signIn')}
        </button>
        <p className="text-center text-sm">
          <Link href={`/${locale}/early-access`} onClick={onClose}>
            {t('requestAccess')}
          </Link>
        </p>
      </form>
    </Modal>
  );
}
