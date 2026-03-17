'use client';

import Link from 'next/link';
import ScrollReveal from '@/components/ScrollReveal';

/* ── Inline SVG Icon Components ──────────── */
function IconSignal() {
    return (
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M2 12h2l3-9 4 18 4-18 3 9h4" />
        </svg>
    );
}

function IconChart() {
    return (
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 3v18h18" />
            <path d="M7 16l4-6 4 4 5-8" />
        </svg>
    );
}

function IconLeaf() {
    return (
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 22c-4-4-8-7.4-8-12 0-4.4 3.6-8 8-8s8 3.6 8 8c0 4.6-4 8-8 12z" />
            <path d="M12 10v6" />
            <path d="M9 13l3-3 3 3" />
        </svg>
    );
}

export default function LandingPage() {
    return (
        <div className="min-h-screen">
            {/* ═══════════════════════════════════════════
                 HERO — Vision + Emotional Hook
                 1 message: What is the vision?
            ═══════════════════════════════════════════ */}
            <section className="relative pt-36 pb-32 px-6 overflow-hidden">
                <div className="relative z-10 max-w-[1280px] mx-auto text-center">
                    <ScrollReveal variant="fade-in" delay={100}>
                        <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-gm-green-50/80 backdrop-blur-sm text-gm-green-600 text-sm font-medium mb-8 border border-gm-green-200/40">
                            <span className="w-1.5 h-1.5 rounded-full bg-gm-green-500 animate-pulse" />
                            Now in Beta
                        </div>
                    </ScrollReveal>

                    <ScrollReveal variant="fade-up" delay={200}>
                        <h1 className="text-5xl md:text-8xl font-bold text-apple-gray-800 tracking-tight leading-[1.05] mb-8 mt-12 md:mt-0">
                            Giving plants<br />
                            <span className="gradient-text">a voice.</span>
                        </h1>
                    </ScrollReveal>

                    <ScrollReveal variant="fade-up" delay={400}>
                        <p className="text-lg md:text-2xl text-apple-gray-400 max-w-2xl mx-auto leading-relaxed mb-14 px-2 md:px-0">
                            Emerging research in plant electrophysiology suggests we can detect
                            stress before visible symptoms appear. GreenMind makes this science accessible.
                        </p>
                    </ScrollReveal>

                    <ScrollReveal variant="fade-up" delay={550}>
                        <div className="flex flex-col md:flex-row items-center justify-center gap-4">
                            <Link
                                href="mailto:traver.dinten@outlook.com?subject=Early Access Request - GreenMind&body=Name:%0D%0ACompany:%0D%0A%0D%0ATell us about your greenhouse operation:"
                                className="btn-glow w-full md:w-auto px-8 py-4 bg-gm-green-500 text-white rounded-full text-lg font-medium hover:bg-gm-green-600 transition-all duration-300 shadow-lg shadow-gm-green-500/20"
                            >
                                Start monitoring plant signals
                            </Link>
                            <Link
                                href="/product"
                                className="w-full md:w-auto px-8 py-4 text-apple-gray-600 rounded-full text-lg font-medium hover:bg-apple-gray-100 transition-all duration-300"
                            >
                                Explore the Platform →
                            </Link>
                        </div>
                    </ScrollReveal>
                </div>
            </section>

            {/* ═══════════════════════════════════════════
                 PROBLEM — The Blind Spot
                 1 message: What problem exists?
            ═══════════════════════════════════════════ */}
            <section className="py-28 px-6">
                <div className="max-w-[960px] mx-auto text-center">
                    <ScrollReveal>
                        <p className="text-sm font-semibold text-gm-green-600 uppercase tracking-widest mb-4">
                            The Challenge
                        </p>
                    </ScrollReveal>
                    <ScrollReveal delay={150}>
                        <h2 className="text-3xl md:text-5xl font-bold text-apple-gray-800 tracking-tight leading-tight mb-6">
                            Most crop damage is detected<br className="hidden md:block" />
                            <span className="text-apple-gray-300">after it&apos;s already happened.</span>
                        </h2>
                    </ScrollReveal>
                    <ScrollReveal delay={300}>
                        <p className="text-lg text-apple-gray-400 max-w-2xl mx-auto leading-relaxed">
                            Traditional greenhouse monitoring tracks temperature, humidity, and soil —
                            but not the plant itself. By the time stress becomes visible, yield is already compromised.
                            Current research suggests that biological signals may hold the key to earlier detection.
                        </p>
                    </ScrollReveal>
                </div>
            </section>

            {/* ═══════════════════════════════════════════
                 SOLUTION — Feature Cards
                 1 message: How does GreenMind solve it?
            ═══════════════════════════════════════════ */}
            <section className="py-28 px-6 bg-apple-gray-100">
                <div className="max-w-[1280px] mx-auto">
                    <ScrollReveal>
                        <div className="text-center mb-16">
                            <p className="text-sm font-semibold text-gm-green-600 uppercase tracking-widest mb-4">
                                The Solution
                            </p>
                            <h2 className="text-3xl md:text-5xl font-bold text-apple-gray-800 mb-4 tracking-tight">
                                The science of smarter cultivation.
                            </h2>
                            <p className="text-lg text-apple-gray-400 max-w-xl mx-auto">
                                Every plant tells a story. GreenMind helps you read it — and act on it.
                            </p>
                        </div>
                    </ScrollReveal>
                    <div className="grid md:grid-cols-3 gap-6">
                        <ScrollReveal delay={100}>
                            <FeatureCard
                                icon={<IconSignal />}
                                title="Early Stress Detection"
                                description="Early signals from ongoing research suggest that biological indicators may reveal water deficit, nutrient imbalance, and pathogen pressure before visible symptoms appear."
                            />
                        </ScrollReveal>
                        <ScrollReveal delay={250}>
                            <FeatureCard
                                icon={<IconChart />}
                                title="Predictive Yield Intelligence"
                                description="Combine environmental and biological trend data to build yield models — an emerging approach designed to support smarter resource allocation and harvest planning."
                            />
                        </ScrollReveal>
                        <ScrollReveal delay={400}>
                            <FeatureCard
                                icon={<IconLeaf />}
                                title="Precision Resource Management"
                                description="Use continuous data to inform water, nutrient, and energy decisions. Our goal: reduce waste and support more sustainable cultivation practices."
                            />
                        </ScrollReveal>
                    </div>
                </div>
            </section>

            {/* ═══════════════════════════════════════════
                 HOW IT WORKS — Visual Timeline
                 1 message: How does the process work?
            ═══════════════════════════════════════════ */}
            <section className="py-28 px-6">
                <div className="max-w-[1280px] mx-auto">
                    <ScrollReveal>
                        <div className="text-center mb-20">
                            <p className="text-sm font-semibold text-gm-green-600 uppercase tracking-widest mb-4">
                                How It Works
                            </p>
                            <h2 className="text-3xl md:text-5xl font-bold text-apple-gray-800 mb-4 tracking-tight">
                                From signal to strategy.
                            </h2>
                            <p className="text-lg text-apple-gray-400 max-w-2xl mx-auto">
                                Plants respond to their environment continuously. These responses carry
                                measurable information. GreenMind makes it actionable.
                            </p>
                        </div>
                    </ScrollReveal>
                    <div className="grid md:grid-cols-4 gap-8">
                        {[
                            { step: '01', title: 'Plants React', desc: 'Research shows that plants continuously generate measurable biological responses to environmental changes — signals that may carry diagnostic value.' },
                            { step: '02', title: 'Signals Captured', desc: 'Non-invasive sensing records plant vitals alongside climate data — building a synchronized, high-resolution dataset for analysis.' },
                            { step: '03', title: 'Intelligence Emerges', desc: 'Analytics identify patterns, anomalies, and emerging correlations — an ongoing scientific investigation into predictive cultivation.' },
                            { step: '04', title: 'Growers Benefit', desc: 'Data-driven insights aim to help growers adjust earlier and more precisely — supporting better yield outcomes and sustainability.' },
                        ].map((item, i) => (
                            <ScrollReveal key={item.step} delay={i * 150}>
                                <div className="timeline-step text-center">
                                    <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-gm-green-50 mb-5">
                                        <span className="text-2xl font-bold gradient-text">{item.step}</span>
                                    </div>
                                    <h3 className="text-lg font-semibold text-apple-gray-800 mb-2">{item.title}</h3>
                                    <p className="text-sm text-apple-gray-400 leading-relaxed">{item.desc}</p>
                                </div>
                            </ScrollReveal>
                        ))}
                    </div>
                </div>
            </section>

            {/* ═══════════════════════════════════════════
                 TRUST — Social Proof & Authority
                 1 message: Why should you trust us?
            ═══════════════════════════════════════════ */}
            <section className="py-28 px-6 bg-apple-gray-100">
                <div className="max-w-[1280px] mx-auto">
                    <ScrollReveal>
                        <div className="text-center mb-16">
                            <p className="text-sm font-semibold text-gm-green-600 uppercase tracking-widest mb-4">
                                Built on Science
                            </p>
                            <h2 className="text-3xl md:text-5xl font-bold text-apple-gray-800 mb-4 tracking-tight">
                                Rooted in research. Tested in the field.
                            </h2>
                            <p className="text-lg text-apple-gray-400 max-w-2xl mx-auto">
                                Developed in collaboration with applied agricultural research at FHNW —
                                currently being tested and validated in real greenhouse environments.
                            </p>
                        </div>
                    </ScrollReveal>

                    <div className="grid md:grid-cols-3 gap-8 max-w-[960px] mx-auto">
                        <ScrollReveal delay={100}>
                            <div className="text-center p-8">
                                <div className="text-4xl md:text-5xl font-bold gradient-text stat-value mb-2">FHNW</div>
                                <p className="text-sm text-apple-gray-400">University of Applied Sciences<br />Northwestern Switzerland</p>
                            </div>
                        </ScrollReveal>
                        <ScrollReveal delay={250}>
                            <div className="text-center p-8">
                                <div className="text-4xl md:text-5xl font-bold gradient-text stat-value mb-2">24/7</div>
                                <p className="text-sm text-apple-gray-400">Continuous biological<br />signal monitoring</p>
                            </div>
                        </ScrollReveal>
                        <ScrollReveal delay={400}>
                            <div className="text-center p-8">
                                <div className="text-4xl md:text-5xl font-bold gradient-text stat-value mb-2">Basel</div>
                                <p className="text-sm text-apple-gray-400">Swiss precision engineering<br />from the innovation hub</p>
                            </div>
                        </ScrollReveal>
                    </div>
                </div>
            </section>

            {/* ═══════════════════════════════════════════
                 BENEFITS — Why Growers Choose Us
                 1 message: What are the outcomes?
            ═══════════════════════════════════════════ */}
            <section className="py-28 px-6">
                <div className="max-w-[1280px] mx-auto">
                    <ScrollReveal>
                        <div className="text-center mb-16">
                            <p className="text-sm font-semibold text-gm-green-600 uppercase tracking-widest mb-4">
                                Benefits
                            </p>
                            <h2 className="text-3xl md:text-5xl font-bold text-apple-gray-800 mb-4 tracking-tight">
                                Why growers choose GreenMind.
                            </h2>
                        </div>
                    </ScrollReveal>
                    <div className="flex hide-scrollbar overflow-x-auto snap-x snap-mandatory gap-4 md:grid md:grid-cols-3 md:gap-6 pb-4 md:pb-0 -mx-6 px-6 md:mx-0 md:px-0">
                        {[
                            { title: 'Healthier Plants', desc: 'Gain deeper insight into plant condition — early signals may help you intervene before stress becomes visible damage.' },
                            { title: 'Higher Yield Predictability', desc: 'Build yield models using biological and environmental trend data — an emerging approach to more confident harvest planning.' },
                            { title: 'Precision Resource Use', desc: 'Inform water and nutrient decisions with continuous data — designed to reduce waste through more precise resource management.' },
                            { title: 'Faster Risk Response', desc: 'Early indicators may flag disease pressure, heat stress, or irrigation issues — enabling faster response times.' },
                            { title: 'Sustainable Operations', desc: 'Work toward a lower ecological footprint through data-informed energy, water, and input optimization.' },
                            { title: 'Smarter Decisions', desc: 'Move from guesswork toward continuous, science-based intelligence across your cultivation operation.' },
                        ].map((b, i) => (
                            <ScrollReveal key={b.title} delay={i * 80} className="w-[85vw] md:w-auto flex-shrink-0 snap-center">
                                <div className="card-hover bg-white rounded-apple-lg p-6 shadow-apple-card h-full flex flex-col justify-between">
                                    <div>
                                        <h3 className="text-base font-semibold text-apple-gray-800 mb-2">{b.title}</h3>
                                        <p className="text-sm text-apple-gray-400 leading-relaxed">{b.desc}</p>
                                    </div>
                                </div>
                            </ScrollReveal>
                        ))}
                    </div>
                </div>
            </section>

            {/* ═══════════════════════════════════════════
                 CTA — Final Conversion
                 1 message: Take the next step.
            ═══════════════════════════════════════════ */}
            <section className="py-28 px-6 bg-apple-gray-800">
                <div className="max-w-[1280px] mx-auto text-center">
                    <ScrollReveal>
                        <p className="text-sm font-semibold text-gm-green-400 uppercase tracking-widest mb-4">
                            Get Started
                        </p>
                    </ScrollReveal>
                    <ScrollReveal delay={150}>
                        <h2 className="text-3xl md:text-5xl font-bold text-white mb-6 tracking-tight leading-tight">
                            The future of cultivation<br className="hidden md:block" />
                            is listening.
                        </h2>
                    </ScrollReveal>
                    <ScrollReveal delay={300}>
                        <p className="text-lg text-apple-gray-300 mb-10 max-w-xl mx-auto leading-relaxed">
                            Join forward-thinking growers building more sustainable,
                            productive, and intelligent cultivation operations.
                        </p>
                    </ScrollReveal>
                    <ScrollReveal delay={450}>
                        <Link
                            href="mailto:traver.dinten@outlook.com?subject=Early Access Request - GreenMind&body=Name:%0D%0ACompany:%0D%0A%0D%0ATell us about your greenhouse operation:"
                            className="btn-glow inline-flex px-10 py-4 bg-gm-green-500 text-white rounded-full text-lg font-medium hover:bg-gm-green-400 transition-colors duration-300"
                        >
                            Start monitoring plant signals
                        </Link>
                    </ScrollReveal>
                </div>
            </section>

            {/* ═══════════════════════════════════════════
                 FOOTER
            ═══════════════════════════════════════════ */}
            <footer className="py-16 px-6 border-t border-apple-gray-200">
                <div className="max-w-[1280px] mx-auto">
                    <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-8">
                        <div className="flex items-center gap-2">
                            <div className="w-7 h-7 rounded-lg bg-gm-green-500 flex items-center justify-center">
                                <span className="text-white text-sm font-bold">G</span>
                            </div>
                            <span className="font-semibold text-apple-gray-800 tracking-tight">GreenMind</span>
                        </div>

                        <div className="flex flex-wrap items-center gap-x-8 gap-y-3 text-sm text-apple-gray-400">
                            <Link href="/product" className="hover:text-apple-gray-800 transition-colors duration-200">Product</Link>
                            <Link href="/technology" className="hover:text-apple-gray-800 transition-colors duration-200">Technology</Link>
                            <Link href="/about" className="hover:text-apple-gray-800 transition-colors duration-200">About</Link>
                            <Link href="/contact" className="hover:text-apple-gray-800 transition-colors duration-200">Contact</Link>
                        </div>
                    </div>

                    <div className="mt-8 pt-8 border-t border-apple-gray-200/50 flex flex-col md:flex-row items-center justify-between gap-4">
                        <p className="text-sm text-apple-gray-400">© 2026 GreenMind. All rights reserved.</p>
                        <p className="text-sm text-apple-gray-300">FHNW Campus Muttenz · Basel, Switzerland</p>
                    </div>
                </div>
            </footer>
        </div>
    );
}


function FeatureCard({ icon, title, description }: { icon: React.ReactNode; title: string; description: string }) {
    return (
        <div className="card-hover bg-white rounded-apple-lg p-8 shadow-apple-card h-full">
            <div className="icon-container mb-5 text-gm-green-600">{icon}</div>
            <h3 className="text-lg font-semibold text-apple-gray-800 mb-2">{title}</h3>
            <p className="text-sm text-apple-gray-400 leading-relaxed">{description}</p>
        </div>
    );
}
