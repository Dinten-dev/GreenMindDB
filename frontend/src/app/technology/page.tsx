'use client';

import Link from 'next/link';
import ScrollReveal from '@/components/ScrollReveal';

export default function TechnologyPage() {
    return (
        <div className="min-h-screen">
            <div className="pt-28 pb-24 px-6 max-w-[1280px] mx-auto">
                <ScrollReveal>
                    <p className="text-sm font-semibold text-gm-green-600 uppercase tracking-widest mb-4">Technology</p>
                    <h1 className="text-4xl md:text-6xl font-bold text-apple-gray-800 mb-6 tracking-tight">The Science Behind GreenMind.</h1>
                </ScrollReveal>
                <ScrollReveal delay={200}>
                    <p className="text-xl text-apple-gray-400 max-w-2xl mb-20 leading-relaxed">
                        Grounded in plant electrophysiology research. Engineered for modern agriculture.
                    </p>
                </ScrollReveal>

                <div className="space-y-24">
                    <ScrollReveal>
                        <section className="max-w-3xl">
                            <h2 className="text-2xl md:text-3xl font-semibold text-apple-gray-800 mb-6">Listening to Plants</h2>
                            <p className="text-apple-gray-400 leading-relaxed mb-4">
                                Plants are not passive. They continuously generate measurable biological responses
                                to their environment — reacting to changes in light, water availability, temperature,
                                nutrient levels, and pathogen pressure. Current research suggests these responses may
                                carry valuable diagnostic information that has historically been invisible to growers.
                            </p>
                            <p className="text-apple-gray-400 leading-relaxed">
                                GreenMind aims to capture these signals at the source through non-invasive sensing
                                technology — building a continuous, high-resolution dataset that complements traditional
                                environmental monitoring and opens new avenues for scientific investigation.
                            </p>
                        </section>
                    </ScrollReveal>

                    <section>
                        <ScrollReveal>
                            <h2 className="text-2xl md:text-3xl font-semibold text-apple-gray-800 mb-8">From Signal to Strategy</h2>
                        </ScrollReveal>
                        <div className="flex hide-scrollbar overflow-x-auto snap-x snap-mandatory gap-4 md:grid md:grid-cols-3 md:gap-6 pb-4 md:pb-0 -mx-6 px-6 md:mx-0 md:px-0">
                            {[
                                { title: 'Biological Signal Capture', desc: 'Precision sensing units record plant vitals and environmental variables simultaneously — building a synchronized, multi-dimensional dataset of greenhouse conditions.' },
                                { title: 'Intelligent Processing', desc: 'Scalable analytics infrastructure processes continuous data streams — identifying emerging patterns, anomalies, and potential early warning indicators.' },
                                { title: 'Emerging Cultivation Insights', desc: 'Ongoing research aims to translate biological and environmental data into actionable insights — supporting growers in making earlier, more informed cultivation decisions.' },
                            ].map((item, i) => (
                                <ScrollReveal key={item.title} delay={i * 150} className="w-[85vw] md:w-auto flex-shrink-0 snap-center">
                                    <div className="card-hover bg-apple-gray-100 rounded-apple-lg p-8 h-full">
                                        <h3 className="font-semibold text-apple-gray-800 mb-3">{item.title}</h3>
                                        <p className="text-sm text-apple-gray-400 leading-relaxed">{item.desc}</p>
                                    </div>
                                </ScrollReveal>
                            ))}
                        </div>
                    </section>
                </div>
            </div>
        </div>
    );
}
