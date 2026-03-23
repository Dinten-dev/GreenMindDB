'use client';

import Link from 'next/link';
import { useState, useEffect } from 'react';
import { usePathname } from 'next/navigation';

export default function Navbar() {
    const [isMenuOpen, setIsMenuOpen] = useState(false);
    const pathname = usePathname();

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
        }
    }, [isMenuOpen]);

    const navLinks = [
        { name: 'Produkt', href: '/product' },
        { name: 'Technologie', href: '/technology' },
        { name: 'Über uns', href: '/about' },
        { name: 'Kontakt', href: '/contact' }
    ];

    return (
        <>
            {/* ── Navbar ──────────────────────────── */}
            <nav className="fixed top-0 left-0 right-0 z-[100] bg-white/80 backdrop-blur-xl border-b border-apple-gray-200/50">
                <div className="max-w-[1280px] mx-auto px-6 h-14 flex items-center justify-between">
                    {/* Logo */}
                    <Link href="/" className="flex items-center gap-2 relative z-[110]">
                        <div className="w-7 h-7 rounded-lg bg-gm-green-500 flex items-center justify-center">
                            <span className="text-white text-sm font-bold">G</span>
                        </div>
                        <span className="font-semibold text-apple-gray-800 tracking-tight">GreenMind</span>
                    </Link>

                    {/* Desktop Navigation */}
                    <div className="hidden md:flex items-center gap-8 text-sm text-apple-gray-500">
                        {navLinks.map((link) => (
                            <Link
                                key={link.href}
                                href={link.href}
                                className={`transition-colors duration-200 ${pathname === link.href ? 'text-apple-gray-800 font-medium' : 'hover:text-apple-gray-800'}`}
                            >
                                {link.name}
                            </Link>
                        ))}
                    </div>

                    {/* Desktop CTAs */}
                    <div className="hidden md:flex items-center gap-3 relative z-[110]">
                        <Link href="/login" className="text-sm text-apple-gray-500 hover:text-apple-gray-800 transition-colors duration-200">
                            Anmelden
                        </Link>
                        <Link
                            href="/early-access"
                            className="text-sm px-4 py-2 bg-gm-green-500 text-white rounded-full font-medium hover:bg-gm-green-600 transition-colors duration-200"
                        >
                            Loslegen
                        </Link>
                    </div>

                    {/* Mobile Hamburger Button */}
                    <button
                        className="md:hidden p-2 -mr-2 relative z-[110] text-apple-gray-800"
                        onClick={() => setIsMenuOpen(!isMenuOpen)}
                        aria-label="Menü umschalten"
                    >
                        <div className="w-6 h-5 flex flex-col justify-between items-end">
                            <span className={`h-[2px] w-full bg-current rounded-full transition-all duration-300 ${isMenuOpen ? 'rotate-45 translate-y-[9px]' : ''}`} />
                            <span className={`h-[2px] w-full bg-current rounded-full transition-all duration-300 ${isMenuOpen ? 'opacity-0' : ''}`} />
                            <span className={`h-[2px] w-full bg-current rounded-full transition-all duration-300 ${isMenuOpen ? '-rotate-45 -translate-y-[9px]' : ''}`} />
                        </div>
                    </button>
                </div>
            </nav>

            {/* ── Mobile Menu Overlay ─────────────── */}
            <div
                className={`fixed inset-0 bg-white z-[90] flex flex-col pt-24 px-6 pb-8 transition-transform duration-500 ease-[cubic-bezier(0.32,0.72,0,1)] md:hidden ${isMenuOpen ? 'translate-y-0' : '-translate-y-full'}`}
            >
                <div className="flex flex-col gap-6 text-2xl font-semibold tracking-tight text-apple-gray-800">
                    {navLinks.map((link) => (
                        <Link
                            key={link.href}
                            href={link.href}
                            className={`pb-4 border-b border-apple-gray-100 ${pathname === link.href ? 'text-gm-green-600' : ''}`}
                        >
                            {link.name}
                        </Link>
                    ))}
                </div>

                <div className="mt-auto flex flex-col gap-4">
                    <Link
                        href="/login"
                        className="w-full py-4 text-center rounded-xl bg-apple-gray-100 text-apple-gray-800 font-medium text-lg"
                    >
                        Anmelden
                    </Link>
                    <Link
                        href="/early-access"
                        className="w-full py-4 text-center rounded-xl bg-gm-green-500 text-white font-medium text-lg shadow-lg shadow-gm-green-500/20"
                    >
                        Loslegen
                    </Link>
                </div>
            </div>
        </>
    );
}
