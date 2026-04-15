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
                 HERO
            ═══════════════════════════════════════════ */}
            <section className="relative pt-36 pb-32 px-6 overflow-hidden">
                <div className="relative z-10 max-w-[1280px] mx-auto text-center">
                    <ScrollReveal variant="fade-in" delay={100}>
                        <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-amber-50/80 backdrop-blur-sm text-amber-700 text-sm font-medium mb-8 border border-amber-200/40">
                            <span className="w-1.5 h-1.5 rounded-full bg-amber-500 animate-pulse" />
                            In Entwicklung
                        </div>
                    </ScrollReveal>

                    <ScrollReveal variant="fade-up" delay={200}>
                        <h1 className="text-5xl md:text-8xl font-bold text-apple-gray-800 tracking-tight leading-[1.05] mb-8 mt-12 md:mt-0">
                            Pflanzen eine<br />
                            <span className="gradient-text">Stimme geben.</span>
                        </h1>
                    </ScrollReveal>

                    <ScrollReveal variant="fade-up" delay={400}>
                        <p className="text-lg md:text-2xl text-apple-gray-500 max-w-2xl mx-auto leading-relaxed mb-14 px-2 md:px-0">
                            GreenMind analysiert bioelektrische Pflanzensignale, um Stress früher zu erkennen
                            und Anbaustrategien gezielt zu verbessern.
                        </p>
                    </ScrollReveal>

                    <ScrollReveal variant="fade-up" delay={550}>
                        <div className="flex flex-col md:flex-row items-center justify-center gap-4">
                            <Link
                                href="/early-access"
                                className="btn-glow w-full md:w-auto px-8 py-4 bg-gm-green-500 text-white rounded-full text-lg font-medium hover:bg-gm-green-600 transition-all duration-300 shadow-lg shadow-gm-green-500/20"
                            >
                                Zugang anfragen
                            </Link>
                            <Link
                                href="/technology"
                                className="w-full md:w-auto px-8 py-4 text-apple-gray-700 rounded-full text-lg font-medium hover:bg-apple-gray-100 transition-all duration-300"
                            >
                                So funktioniert&apos;s →
                            </Link>
                        </div>
                    </ScrollReveal>
                </div>
            </section>

            {/* ═══════════════════════════════════════════
                 PROBLEM
            ═══════════════════════════════════════════ */}
            <section className="py-28 px-6">
                <div className="max-w-[960px] mx-auto text-center">
                    <ScrollReveal>
                        <p className="text-sm font-semibold text-gm-green-600 uppercase tracking-widest mb-4">
                            Das Problem
                        </p>
                    </ScrollReveal>
                    <ScrollReveal delay={150}>
                        <h2 className="text-3xl md:text-5xl font-bold text-apple-gray-800 tracking-tight leading-tight mb-6">
                            Pflanzenstress wird erkannt,<br className="hidden md:block" />
                            <span className="text-apple-gray-500">wenn der Schaden bereits sichtbar ist.</span>
                        </h2>
                    </ScrollReveal>
                    <ScrollReveal delay={300}>
                        <p className="text-lg text-apple-gray-500 max-w-2xl mx-auto leading-relaxed">
                            Heute überwachen Sensoren die Umgebung — Temperatur, Luftfeuchtigkeit,
                            Bodenfeuchtigkeit. Aber nicht die Pflanze selbst. Dabei senden Pflanzen
                            bioelektrische Signale, die auf Stress hinweisen — oft bevor äussere
                            Symptome sichtbar werden.
                        </p>
                    </ScrollReveal>
                </div>
            </section>

            {/* ═══════════════════════════════════════════
                 LÖSUNG
            ═══════════════════════════════════════════ */}
            <section className="py-28 px-6 bg-apple-gray-100">
                <div className="max-w-[1280px] mx-auto">
                    <ScrollReveal>
                        <div className="text-center mb-16">
                            <p className="text-sm font-semibold text-gm-green-600 uppercase tracking-widest mb-4">
                                Unser Ansatz
                            </p>
                            <h2 className="text-3xl md:text-5xl font-bold text-apple-gray-800 mb-4 tracking-tight">
                                Pflanzensignale verstehen. Gezielter handeln.
                            </h2>
                            <p className="text-lg text-apple-gray-500 max-w-xl mx-auto">
                                GreenMind erfasst bioelektrische Signale direkt an der Pflanze und macht
                                sie für datengestützte Anbauentscheidungen nutzbar.
                            </p>
                        </div>
                    </ScrollReveal>
                    <div className="grid md:grid-cols-3 gap-6">
                        <ScrollReveal delay={100}>
                            <FeatureCard
                                icon={<IconSignal />}
                                title="Frühe Stresserkennung"
                                description="Bioelektrische Signale können Dürre-, Schädlings- und Nährstoffstress anzeigen — mit dem Ziel, Probleme zu erkennen, bevor sichtbare Schäden entstehen."
                            />
                        </ScrollReveal>
                        <ScrollReveal delay={250}>
                            <FeatureCard
                                icon={<IconChart />}
                                title="Datenbasierte Ertragsplanung"
                                description="Pflanzensignale in Kombination mit Umweltdaten ermöglichen neue Ansätze für die Ertragsplanung — datengestützt statt rein erfahrungsbasiert."
                            />
                        </ScrollReveal>
                        <ScrollReveal delay={400}>
                            <FeatureCard
                                icon={<IconLeaf />}
                                title="Gezielter Ressourceneinsatz"
                                description="Wenn die Pflanze signalisiert, was sie braucht, lassen sich Wasser und Nährstoffe bedarfsgerecht einsetzen — weniger Verbrauch, bessere Ergebnisse."
                            />
                        </ScrollReveal>
                    </div>
                </div>
            </section>

            {/* ═══════════════════════════════════════════
                 WIE ES FUNKTIONIERT
            ═══════════════════════════════════════════ */}
            <section className="py-28 px-6">
                <div className="max-w-[1280px] mx-auto">
                    <ScrollReveal>
                        <div className="text-center mb-20">
                            <p className="text-sm font-semibold text-gm-green-600 uppercase tracking-widest mb-4">
                                So funktioniert&apos;s
                            </p>
                            <h2 className="text-3xl md:text-5xl font-bold text-apple-gray-800 mb-4 tracking-tight">
                                Vom Signal zur Empfehlung.
                            </h2>
                            <p className="text-lg text-apple-gray-500 max-w-2xl mx-auto">
                                Pflanzen reagieren auf ihre Umgebung mit messbaren elektrischen Signalen.
                                GreenMind macht diese Signale sichtbar und analysierbar.
                            </p>
                        </div>
                    </ScrollReveal>
                    <div className="grid md:grid-cols-4 gap-8">
                        {[
                            { step: '01', title: 'Pflanzensignal', desc: 'Pflanzen erzeugen elektrische Signale als Reaktion auf Veränderungen in ihrer Umgebung — Licht, Wasser, Temperatur, Nährstoffe.' },
                            { step: '02', title: 'Sensorik', desc: 'Sensoren an der Pflanze erfassen diese Signale nicht-invasiv und kombinieren sie mit Umgebungsdaten wie Temperatur und Bodenfeuchtigkeit.' },
                            { step: '03', title: 'Analyse', desc: 'Die Daten werden automatisch aufbereitet, gefiltert und auf Muster untersucht — welche Signale deuten auf welchen Stress hin?' },
                            { step: '04', title: 'Empfehlung', desc: 'Erkenntnisse werden in konkrete Handlungsempfehlungen übersetzt — wann giessen, wann düngen, wann eingreifen.' },
                        ].map((item, i) => (
                            <ScrollReveal key={item.step} delay={i * 150}>
                                <div className="timeline-step text-center">
                                    <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-gm-green-50 mb-5">
                                        <span className="text-2xl font-bold gradient-text">{item.step}</span>
                                    </div>
                                    <h3 className="text-lg font-semibold text-apple-gray-800 mb-2">{item.title}</h3>
                                    <p className="text-sm text-apple-gray-500 leading-relaxed">{item.desc}</p>
                                </div>
                            </ScrollReveal>
                        ))}
                    </div>
                </div>
            </section>

            {/* ═══════════════════════════════════════════
                 FUNDAMENT
            ═══════════════════════════════════════════ */}
            <section className="py-28 px-6 bg-apple-gray-100">
                <div className="max-w-[1280px] mx-auto">
                    <ScrollReveal>
                        <div className="text-center mb-16">
                            <p className="text-sm font-semibold text-gm-green-600 uppercase tracking-widest mb-4">
                                Fundament
                            </p>
                            <h2 className="text-3xl md:text-5xl font-bold text-apple-gray-800 mb-4 tracking-tight">
                                Wissenschaftlich fundiert. Im Feld erprobt.
                            </h2>
                            <p className="text-lg text-apple-gray-500 max-w-2xl mx-auto">
                                Entwickelt in Zusammenarbeit mit der FHNW und aufbauend auf
                                peer-reviewed Forschung zur Pflanzenelektrophysiologie.
                            </p>
                        </div>
                    </ScrollReveal>

                    <div className="grid md:grid-cols-3 gap-8 max-w-[960px] mx-auto">
                        <ScrollReveal delay={100}>
                            <div className="text-center p-8">
                                <div className="text-4xl md:text-5xl font-bold gradient-text stat-value mb-2">FHNW</div>
                                <p className="text-sm text-apple-gray-500">Fachhochschule<br />Nordwestschweiz</p>
                            </div>
                        </ScrollReveal>
                        <ScrollReveal delay={250}>
                            <div className="text-center p-8">
                                <div className="text-4xl md:text-5xl font-bold gradient-text stat-value mb-2">24/7</div>
                                <p className="text-sm text-apple-gray-500">Kontinuierliche<br />Datenerfassung</p>
                            </div>
                        </ScrollReveal>
                        <ScrollReveal delay={400}>
                            <div className="text-center p-8">
                                <div className="text-4xl md:text-5xl font-bold gradient-text stat-value mb-2">Aarau</div>
                                <p className="text-sm text-apple-gray-500">Entwicklungsstandort<br />Schweiz</p>
                            </div>
                        </ScrollReveal>
                    </div>
                </div>
            </section>

            {/* ═══════════════════════════════════════════
                 POTENZIALE
            ═══════════════════════════════════════════ */}
            <section className="py-28 px-6">
                <div className="max-w-[1280px] mx-auto">
                    <ScrollReveal>
                        <div className="text-center mb-16">
                            <p className="text-sm font-semibold text-gm-green-600 uppercase tracking-widest mb-4">
                                Potenziale
                            </p>
                            <h2 className="text-3xl md:text-5xl font-bold text-apple-gray-800 mb-4 tracking-tight">
                                Was GreenMind ermöglichen kann.
                            </h2>
                        </div>
                    </ScrollReveal>
                    <div className="flex hide-scrollbar overflow-x-auto snap-x snap-mandatory gap-4 md:grid md:grid-cols-3 md:gap-6 pb-4 md:pb-0 -mx-6 px-6 md:mx-0 md:px-0">
                        {[
                            { title: 'Frühzeitige Stresserkennung', desc: 'Pflanzenstress erkennen, bevor er sichtbar wird — durch die Analyse bioelektrischer Signalmuster.' },
                            { title: 'Bessere Ertragsplanung', desc: 'Pflanzensignale und Umweltdaten kombiniert ermöglichen fundiertere Prognosen für die Erntevorbereitung.' },
                            { title: 'Gezieltes Bewässern und Düngen', desc: 'Wasser und Nährstoffe genau dann einsetzen, wenn die Pflanze es braucht — nicht nach starrem Zeitplan.' },
                            { title: 'Schnellere Risikoerkennung', desc: 'Schädlingsbefall oder Krankheiten frühzeitig identifizieren und gezielt gegensteuern.' },
                            { title: 'Weniger Ressourcenverbrauch', desc: 'Durch bedarfsgerechten Einsatz von Wasser und Energie den ökologischen Fussabdruck reduzieren.' },
                            { title: 'Datengestützte Entscheidungen', desc: 'Vom Bauchgefühl zur Evidenz — Anbauentscheidungen auf Basis realer Pflanzendaten treffen.' },
                        ].map((b, i) => (
                            <ScrollReveal key={b.title} delay={i * 80} className="w-[85vw] md:w-auto flex-shrink-0 snap-center">
                                <div className="card-hover bg-white rounded-apple-lg p-6 shadow-apple-card h-full flex flex-col justify-between">
                                    <div>
                                        <h3 className="text-base font-semibold text-apple-gray-800 mb-2">{b.title}</h3>
                                        <p className="text-sm text-apple-gray-500 leading-relaxed">{b.desc}</p>
                                    </div>
                                </div>
                            </ScrollReveal>
                        ))}
                    </div>
                </div>
            </section>

            {/* ═══════════════════════════════════════════
                 CTA
            ═══════════════════════════════════════════ */}
            <section className="py-28 px-6 bg-apple-gray-800">
                <div className="max-w-[1280px] mx-auto text-center">
                    <ScrollReveal>
                        <p className="text-sm font-semibold text-gm-green-400 uppercase tracking-widest mb-4">
                            Mitmachen
                        </p>
                    </ScrollReveal>
                    <ScrollReveal delay={150}>
                        <h2 className="text-3xl md:text-5xl font-bold text-white mb-6 tracking-tight leading-tight">
                            Die Zukunft der Agrardiagnostik<br className="hidden md:block" />
                            wird datengetrieben.
                        </h2>
                    </ScrollReveal>
                    <ScrollReveal delay={300}>
                        <p className="text-lg text-apple-gray-300 mb-10 max-w-xl mx-auto leading-relaxed">
                            Sie betreiben eine Anbauumgebung und möchten Teil des Projekts werden?
                            Wir suchen Partner für Feldtests und gemeinsame Weiterentwicklung.
                        </p>
                    </ScrollReveal>
                    <ScrollReveal delay={450}>
                        <Link
                            href="/early-access"
                            className="btn-glow inline-flex px-10 py-4 bg-gm-green-500 text-white rounded-full text-lg font-medium hover:bg-gm-green-400 transition-colors duration-300"
                        >
                            Zugang anfragen
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

                        <div className="flex flex-wrap items-center gap-x-8 gap-y-3 text-sm text-apple-gray-500">
                            <Link href="/product" className="hover:text-apple-gray-800 transition-colors duration-200">Plattform</Link>
                            <Link href="/technology" className="hover:text-apple-gray-800 transition-colors duration-200">Technologie</Link>
                            <Link href="/science" className="hover:text-apple-gray-800 transition-colors duration-200">Science</Link>
                            <Link href="/about" className="hover:text-apple-gray-800 transition-colors duration-200">Über uns</Link>
                            <Link href="/contact" className="hover:text-apple-gray-800 transition-colors duration-200">Kontakt</Link>
                        </div>
                    </div>

                    <div className="mt-8 pt-8 border-t border-apple-gray-200/50 flex flex-col md:flex-row items-center justify-between gap-4">
                        <p className="text-sm text-apple-gray-500">© 2026 GreenMind — Galaxyadvisors AG</p>
                        <p className="text-sm text-apple-gray-400">
                            Laurenzenvorstadt 69 · 5000 Aarau ·{' '}
                            <a href="mailto:info@galaxyadvisors.com" className="hover:text-apple-gray-600 transition-colors">info@galaxyadvisors.com</a>
                        </p>
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
            <p className="text-sm text-apple-gray-500 leading-relaxed">{description}</p>
        </div>
    );
}
