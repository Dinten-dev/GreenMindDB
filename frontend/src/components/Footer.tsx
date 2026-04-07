import Link from 'next/link';

export default function Footer() {
    const currentYear = new Date().getFullYear();
    
    return (
        <footer className="w-full border-t border-apple-gray-200/50 bg-white/50 backdrop-blur-md py-8 mt-auto">
            <div className="max-w-[1280px] mx-auto px-6 flex flex-col md:flex-row justify-between items-center gap-4">
                <div className="text-sm text-apple-gray-500">
                    &copy; {currentYear} Galaxyadvisors AG. Alle Rechte vorbehalten.
                </div>
                <div className="flex gap-6 text-sm">
                    <Link href="/contact" className="text-apple-gray-500 hover:text-gm-green-600 transition-colors">
                        Kontakt
                    </Link>
                    <Link href="/impressum" className="text-apple-gray-500 hover:text-gm-green-600 transition-colors">
                        Impressum
                    </Link>
                </div>
            </div>
        </footer>
    );
}
