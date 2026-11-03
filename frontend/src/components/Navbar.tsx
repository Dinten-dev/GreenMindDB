'use client';

import BrandLogo from '@/components/BrandLogo';

import Link from 'next/link';
import { useState, useEffect } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { useAuth } from '@/contexts/AuthContext';
import { localePath, safeReturnTo } from '@/lib/auth-routing';
import { useTranslations, useLocale } from 'next-intl';

export default function Navbar() {
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const { user, loading, status, refresh } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const t = useTranslations('Navigation');
  const auth = useTranslations('Auth');
  const locale = useLocale();

  // Close menu when route changes
  useEffect(() => {
    setIsMenuOpen(false);
  }, [pathname]);

  // Prevent scrolling when mobile menu is open
  useEffect(() => {
    if (isMenuOpen) {
      document.body.style.overflow = 'hidden';
    } else {
      document.body.style.overflow = 'unset';
    }
    return () => {
      document.body.style.overflow = 'unset';
    };
  }, [isMenuOpen]);

  const navLinks = [
    { name: t('platform'), href: `/${locale}/product` },
    { name: t('technology'), href: `/${locale}/technology` },
    { name: t('science'), href: `/${locale}/science` },
    { name: t('about'), href: `/${locale}/about` },
    { name: t('contact'), href: `/${locale}/contact` },
  ];

  const switchLocale = (newLocale: string) => {
    const query = new URLSearchParams(window.location.search);
    if (query.has('next'))
      query.set('next', localePath(newLocale, safeReturnTo(locale, query.get('next'))));
    const suffix = query.toString();
    router.push(`${localePath(newLocale, pathname)}${suffix ? `?${suffix}` : ''}`);
  };

  // Hide public navbar on dashboard routes (dashboard has its own sidebar)
  if (pathname.startsWith(`/${locale}/app`)) return null;

  return (
    <>
      {/* ── Navbar ──────────────────────────── */}
      <nav className="fixed top-0 left-0 right-0 z-[100] bg-white/80 backdrop-blur-xl border-b border-apple-gray-200/50">
        <div className="max-w-[1200px] mx-auto px-6 h-16 flex items-center justify-between gap-6">
          {/* Logo */}
          <Link href={`/${locale}`} className="flex shrink-0 items-center gap-2 relative z-[110]">
            <BrandLogo size={40} />
            <span className="font-semibold text-apple-gray-800 tracking-tight">GreenMind</span>
          </Link>

          {/* Desktop Navigation */}
          <div className="hidden xl:flex items-center gap-6 whitespace-nowrap text-sm text-apple-gray-500">
            {navLinks.map((link) => (
              <Link
                key={link.href}
                href={link.href}
                className={`transition-colors duration-200 ${pathname.includes(link.href) ? 'text-apple-gray-800 font-medium' : 'hover:text-apple-gray-800'}`}
              >
                {link.name}
              </Link>
            ))}
          </div>

          {/* Desktop CTAs */}
          <div className="hidden xl:flex shrink-0 items-center gap-3 whitespace-nowrap relative z-[110]">
            {loading ? (
              <span role="status" className="text-sm text-apple-gray-500">
                {auth('checking')}
              </span>
            ) : status === 'unavailable' ? (
              <button onClick={() => void refresh()} className="text-sm text-apple-gray-600">
                {auth('retrySession')}
              </button>
            ) : user ? (
              <Link
                href={`/${locale}/app/dashboard`}
                className="flex items-center gap-2 text-sm px-4 py-2 bg-gm-green-500 text-white rounded-full font-medium hover:bg-gm-green-600 transition-colors duration-200"
              >
                <span className="w-5 h-5 rounded-full bg-white/20 flex items-center justify-center text-xs font-bold">
                  {(user.name || user.email)[0].toUpperCase()}
                </span>
                {user.name || user.email.split('@')[0]}
              </Link>
            ) : (
              <>
                <select
                  value={locale}
                  onChange={(e) => switchLocale(e.target.value)}
                  className="bg-transparent text-sm text-apple-gray-500 hover:text-apple-gray-800 transition-colors duration-200 cursor-pointer outline-none border-none appearance-none pr-4 relative"
                  style={{
                    backgroundImage: `url("data:image/svg+xml;charset=UTF-8,%3csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3e%3cpolyline points='6 9 12 15 18 9'%3e%3c/polyline%3e%3c/svg%3e")`,
                    backgroundRepeat: 'no-repeat',
                    backgroundPosition: 'right center',
                    backgroundSize: '1em',
                  }}
                >
                  <option value="de">DE</option>
                  <option value="en">EN</option>
                  <option value="fr">FR</option>
                  <option value="it">IT</option>
                </select>
                <Link
                  href={`/${locale}/login`}
                  className="text-sm text-apple-gray-500 hover:text-apple-gray-800 transition-colors duration-200 ml-2"
                >
                  {auth('signIn')}
                </Link>
                <Link
                  href={`/${locale}/early-access`}
                  className="text-sm px-4 py-2 bg-gm-green-500 text-white rounded-full font-medium hover:bg-gm-green-600 transition-colors duration-200"
                >
                  {auth('requestAccess')}
                </Link>
              </>
            )}
          </div>

          {/* Mobile Hamburger Button */}
          <button
            className="xl:hidden p-3 -mr-3 relative z-[110] text-apple-gray-800 min-w-[44px] min-h-[44px] flex items-center justify-center"
            onClick={() => setIsMenuOpen(!isMenuOpen)}
            aria-label="Menü umschalten"
            aria-expanded={isMenuOpen}
            aria-controls="public-mobile-menu"
          >
            <div className="w-6 h-5 flex flex-col justify-between items-end">
              <span
                className={`h-[2px] w-full bg-current rounded-full transition-all duration-300 ${isMenuOpen ? 'rotate-45 translate-y-[9px]' : ''}`}
              />
              <span
                className={`h-[2px] w-full bg-current rounded-full transition-all duration-300 ${isMenuOpen ? 'opacity-0' : ''}`}
              />
              <span
                className={`h-[2px] w-full bg-current rounded-full transition-all duration-300 ${isMenuOpen ? '-rotate-45 -translate-y-[9px]' : ''}`}
              />
            </div>
          </button>
        </div>
      </nav>

      {/* ── Mobile Menu Overlay ─────────────── */}
      <div
        id="public-mobile-menu"
        inert={!isMenuOpen}
        aria-hidden={!isMenuOpen}
        className={`fixed inset-0 bg-white z-[90] flex flex-col gap-6 overflow-y-auto pt-24 px-6 pb-8 transition-transform duration-500 ease-[cubic-bezier(0.32,0.72,0,1)] xl:hidden ${isMenuOpen ? 'translate-y-0' : '-translate-y-full'}`}
      >
        <div className="flex flex-col gap-5 text-xl font-semibold tracking-tight text-apple-gray-800">
          {navLinks.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className={`pb-4 border-b border-apple-gray-100 ${pathname.includes(link.href) ? 'text-gm-green-600' : ''}`}
              onClick={() => setIsMenuOpen(false)}
            >
              {link.name}
            </Link>
          ))}
          <div className="flex gap-4 pb-4 border-b border-apple-gray-100">
            {['de', 'en', 'fr', 'it'].map((l) => (
              <button
                key={l}
                onClick={() => {
                  switchLocale(l);
                  setIsMenuOpen(false);
                }}
                className={`uppercase text-sm font-bold ${locale === l ? 'text-gm-green-600' : 'text-apple-gray-400'}`}
              >
                {l}
              </button>
            ))}
          </div>
        </div>

        <div className="mt-auto flex flex-col gap-4">
          {loading ? (
            <span role="status">{auth('checking')}</span>
          ) : status === 'unavailable' ? (
            <button onClick={() => void refresh()}>{auth('retrySession')}</button>
          ) : user ? (
            <Link
              href={`/${locale}/app/dashboard`}
              className="w-full py-4 text-center rounded-xl bg-gm-green-500 text-white font-medium text-lg shadow-lg shadow-gm-green-500/20"
            >
              Dashboard · {user.name || user.email.split('@')[0]}
            </Link>
          ) : (
            <>
              <Link
                href={`/${locale}/login`}
                className="w-full py-4 text-center rounded-xl bg-apple-gray-100 text-apple-gray-800 font-medium text-lg"
              >
                {auth('signIn')}
              </Link>
              <Link
                href={`/${locale}/early-access`}
                className="w-full py-4 text-center rounded-xl bg-gm-green-500 text-white font-medium text-lg shadow-lg shadow-gm-green-500/20"
              >
                {auth('requestAccess')}
              </Link>
            </>
          )}
        </div>
      </div>
    </>
  );
}
