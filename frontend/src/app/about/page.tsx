'use client';

import Link from 'next/link';
import ScrollReveal from '@/components/ScrollReveal';

export default function AboutPage() {
    return (
        <div className="min-h-screen">
            <div className="pt-28 pb-24 px-6 max-w-[1280px] mx-auto">
                <ScrollReveal>
                    <p className="text-sm font-semibold text-gm-green-600 uppercase tracking-widest mb-4">About</p>
                    <h1 className="text-4xl md:text-6xl font-bold text-apple-gray-800 mb-6 tracking-tight">About GreenMind.</h1>
                </ScrollReveal>
                <ScrollReveal delay={200}>
                    <p className="text-xl text-apple-gray-400 max-w-2xl mb-20 leading-relaxed">
                        Building the intelligence layer for modern greenhouse agriculture.
                    </p>
                </ScrollReveal>

                <div className="max-w-3xl space-y-16">
                    <ScrollReveal>
                        <section>
                            <h2 className="text-2xl md:text-3xl font-semibold text-apple-gray-800 mb-4">Our Mission</h2>
                            <p className="text-apple-gray-400 leading-relaxed">
                                Agriculture feeds the world — but it operates with surprisingly little real-time
                                insight into the organisms it cultivates. GreenMind exists to change that.
                                We believe that understanding plant condition at a biological level has the potential to
                                fundamentally improve how greenhouses operate — enabling earlier intervention, more precise
                                resource use, and ultimately, healthier crops and higher yields.
                            </p>
                        </section>
                    </ScrollReveal>

                    <ScrollReveal>
                        <section>
                            <h2 className="text-2xl md:text-3xl font-semibold text-apple-gray-800 mb-4">Why It Matters</h2>
                            <p className="text-apple-gray-400 leading-relaxed">
                                Global food demand is rising. Water and arable land are under pressure. Climate
                                volatility makes traditional cultivation methods less predictable. Greenhouse growers
                                need new tools — not just to monitor environmental conditions, but to begin understanding
                                how their crops are responding. GreenMind is working toward that understanding, exploring
                                how biological signals can be turned into actionable intelligence for more sustainable,
                                more productive, and more resilient agriculture.
                            </p>
                        </section>
                    </ScrollReveal>

                    <ScrollReveal>
                        <section>
                            <h2 className="text-2xl md:text-3xl font-semibold text-apple-gray-800 mb-4">The Team</h2>
                            <p className="text-apple-gray-400 leading-relaxed">
                                We are a multidisciplinary team of plant scientists, engineers, and product designers based
                                in Basel, Switzerland. Our work is grounded in applied research from FHNW
                                (University of Applied Sciences Northwestern Switzerland) and driven by a deep conviction
                                that precision agriculture can be made more accessible, more intelligent, and more impactful.
                            </p>
                        </section>
                    </ScrollReveal>

                    <ScrollReveal>
                        <section>
                            <h2 className="text-2xl md:text-3xl font-semibold text-apple-gray-800 mb-4">Sustainable Innovation</h2>
                            <p className="text-apple-gray-400 leading-relaxed">
                                We actively collaborate with academic research institutions and commercial growers to ensure
                                our platform addresses real challenges. Every feature we build is tested in real greenhouse
                                environments. Our goal is not technology for its own sake — it is working toward measurable
                                improvement in sustainability, productivity, and crop health.
                            </p>
                        </section>
                    </ScrollReveal>
                </div>
            </div>
        </div>
    );
}
