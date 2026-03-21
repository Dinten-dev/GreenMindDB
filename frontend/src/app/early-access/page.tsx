'use client';

import ScrollReveal from '@/components/ScrollReveal';

export default function EarlyAccessPage() {
    return (
        <div className="min-h-screen">
            <div className="pt-28 pb-24 px-6 max-w-[640px] mx-auto text-center mt-20">
                <ScrollReveal variant="scale-in">
                    <div className="w-16 h-16 rounded-full bg-gm-green-50 flex items-center justify-center mx-auto mb-6">
                        <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="text-gm-green-600">
                            <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z" />
                            <polyline points="22,6 12,13 2,6" />
                        </svg>
                    </div>
                </ScrollReveal>

                <ScrollReveal delay={150}>
                    <h1 className="text-4xl md:text-5xl font-bold text-apple-gray-800 mb-6 tracking-tight">Early Access anfragen</h1>
                </ScrollReveal>
                <ScrollReveal delay={300}>
                    <p className="text-xl text-apple-gray-400 mb-12 leading-relaxed">
                        Gehören Sie zu den ersten Anbauern, die die Zukunft der Gewächshaus-Intelligenz erleben.
                        Senden Sie uns eine E-Mail mit Details zu Ihrem Betrieb und wir melden uns in Kürze bei Ihnen.
                    </p>
                </ScrollReveal>

                <ScrollReveal delay={450}>
                    <a
                        href="mailto:traver.dinten@outlook.com?subject=Early Access Anfrage - GreenMind&body=Name:%0D%0AUnternehmen:%0D%0A%0D%0AErzählen Sie uns von Ihrem Gewächshausbetrieb:"
                        className="btn-glow inline-block px-8 py-4 bg-gm-green-500 text-white rounded-full text-lg font-medium hover:bg-gm-green-600 transition-all duration-300 shadow-lg shadow-gm-green-500/20 hover:shadow-xl hover:shadow-gm-green-500/30"
                    >
                        E-Mail an traver.dinten@outlook.com
                    </a>
                </ScrollReveal>
            </div>
        </div>
    );
}
