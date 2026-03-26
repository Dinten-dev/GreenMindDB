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
                 HERO — Vision + Emotionaler Aufhänger
                 1 Botschaft: Was ist die Vision?
            ═══════════════════════════════════════════ */}
            <section className="relative pt-36 pb-32 px-6 overflow-hidden">
                <div className="relative z-10 max-w-[1280px] mx-auto text-center">
                    <ScrollReveal variant="fade-in" delay={100}>
                        <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-gm-green-50/80 backdrop-blur-sm text-gm-green-600 text-sm font-medium mb-8 border border-gm-green-200/40">
                            <span className="w-1.5 h-1.5 rounded-full bg-gm-green-500 animate-pulse" />
                            Jetzt in der Beta
                        </div>
                    </ScrollReveal>

                    <ScrollReveal variant="fade-up" delay={200}>
                        <h1 className="text-5xl md:text-8xl font-bold text-apple-gray-800 tracking-tight leading-[1.05] mb-8 mt-12 md:mt-0">
                            Pflanzen eine<br />
                            <span className="gradient-text">Stimme geben.</span>
                        </h1>
                    </ScrollReveal>

                    <ScrollReveal variant="fade-up" delay={400}>
                        <p className="text-lg md:text-2xl text-apple-gray-400 max-w-2xl mx-auto leading-relaxed mb-14 px-2 md:px-0">
                            Aktuelle Forschung in der Pflanzenelektrophysiologie zeigt, dass wir Stress erkennen können,
                            bevor sichtbare Symptome auftreten. GreenMind macht diese Wissenschaft zugänglich.
                        </p>
                    </ScrollReveal>

                    <ScrollReveal variant="fade-up" delay={550}>
                        <div className="flex flex-col md:flex-row items-center justify-center gap-4">
                            <Link
                                href="/early-access"
                                className="btn-glow w-full md:w-auto px-8 py-4 bg-gm-green-500 text-white rounded-full text-lg font-medium hover:bg-gm-green-600 transition-all duration-300 shadow-lg shadow-gm-green-500/20"
                            >
                                Pflanzensignale überwachen
                            </Link>
                            <Link
                                href="/product"
                                className="w-full md:w-auto px-8 py-4 text-apple-gray-600 rounded-full text-lg font-medium hover:bg-apple-gray-100 transition-all duration-300"
                            >
                                Plattform entdecken →
                            </Link>
                        </div>
                    </ScrollReveal>
                </div>
            </section>

            {/* ═══════════════════════════════════════════
                 PROBLEM — Der blinde Fleck
                 1 Botschaft: Welches Problem gibt es?
            ═══════════════════════════════════════════ */}
            <section className="py-28 px-6">
                <div className="max-w-[960px] mx-auto text-center">
                    <ScrollReveal>
                        <p className="text-sm font-semibold text-gm-green-600 uppercase tracking-widest mb-4">
                            Die Herausforderung
                        </p>
                    </ScrollReveal>
                    <ScrollReveal delay={150}>
                        <h2 className="text-3xl md:text-5xl font-bold text-apple-gray-800 tracking-tight leading-tight mb-6">
                            Die meisten Ernteschäden werden erkannt,<br className="hidden md:block" />
                            <span className="text-apple-gray-300">wenn es bereits zu spät ist.</span>
                        </h2>
                    </ScrollReveal>
                    <ScrollReveal delay={300}>
                        <p className="text-lg text-apple-gray-400 max-w-2xl mx-auto leading-relaxed">
                            Herkömmliche Gewächshausüberwachung erfasst Temperatur, Luftfeuchtigkeit und Boden —
                            aber nicht die Pflanze selbst. Wenn Stress sichtbar wird, ist der Ertrag bereits beeinträchtigt.
                            Aktuelle Forschung deutet darauf hin, dass biologische Signale der Schlüssel zur Früherkennung sein könnten.
                        </p>
                    </ScrollReveal>
                </div>
            </section>

            {/* ═══════════════════════════════════════════
                 LÖSUNG — Feature-Karten
                 1 Botschaft: Wie löst GreenMind das Problem?
            ═══════════════════════════════════════════ */}
            <section className="py-28 px-6 bg-apple-gray-100">
                <div className="max-w-[1280px] mx-auto">
                    <ScrollReveal>
                        <div className="text-center mb-16">
                            <p className="text-sm font-semibold text-gm-green-600 uppercase tracking-widest mb-4">
                                Die Lösung
                            </p>
                            <h2 className="text-3xl md:text-5xl font-bold text-apple-gray-800 mb-4 tracking-tight">
                                Die Wissenschaft des intelligenteren Anbaus.
                            </h2>
                            <p className="text-lg text-apple-gray-400 max-w-xl mx-auto">
                                Jede Pflanze erzählt eine Geschichte. GreenMind hilft Ihnen, sie zu lesen — und danach zu handeln.
                            </p>
                        </div>
                    </ScrollReveal>
                    <div className="grid md:grid-cols-3 gap-6">
                        <ScrollReveal delay={100}>
                            <FeatureCard
                                icon={<IconSignal />}
                                title="Frühe Stresserkennung"
                                description="Erste Signale aus der laufenden Forschung deuten darauf hin, dass biologische Indikatoren Wasserdefizit, Nährstoffungleichgewicht und Krankheitsdruck erkennen können, bevor sichtbare Symptome auftreten."
                            />
                        </ScrollReveal>
                        <ScrollReveal delay={250}>
                            <FeatureCard
                                icon={<IconChart />}
                                title="Prädiktive Ertragsintelligenz"
                                description="Kombinieren Sie Umwelt- und biologische Trenddaten, um Ertragsmodelle zu erstellen — ein aufkommender Ansatz für intelligentere Ressourcenplanung und Erntevorbereitung."
                            />
                        </ScrollReveal>
                        <ScrollReveal delay={400}>
                            <FeatureCard
                                icon={<IconLeaf />}
                                title="Präzises Ressourcenmanagement"
                                description="Nutzen Sie kontinuierliche Daten für Wasser-, Nährstoff- und Energieentscheidungen. Unser Ziel: Verschwendung reduzieren und nachhaltigere Anbaumethoden unterstützen."
                            />
                        </ScrollReveal>
                    </div>
                </div>
            </section>

            {/* ═══════════════════════════════════════════
                 SO FUNKTIONIERT ES — Visuelle Timeline
                 1 Botschaft: Wie funktioniert der Prozess?
            ═══════════════════════════════════════════ */}
            <section className="py-28 px-6">
                <div className="max-w-[1280px] mx-auto">
                    <ScrollReveal>
                        <div className="text-center mb-20">
                            <p className="text-sm font-semibold text-gm-green-600 uppercase tracking-widest mb-4">
                                So funktioniert es
                            </p>
                            <h2 className="text-3xl md:text-5xl font-bold text-apple-gray-800 mb-4 tracking-tight">
                                Vom Signal zur Strategie.
                            </h2>
                            <p className="text-lg text-apple-gray-400 max-w-2xl mx-auto">
                                Pflanzen reagieren kontinuierlich auf ihre Umgebung. Diese Reaktionen tragen
                                messbare Informationen. GreenMind macht sie nutzbar.
                            </p>
                        </div>
                    </ScrollReveal>
                    <div className="grid md:grid-cols-4 gap-8">
                        {[
                            { step: '01', title: 'Pflanzen reagieren', desc: 'Forschung zeigt, dass Pflanzen kontinuierlich messbare biologische Reaktionen auf Umweltveränderungen erzeugen — Signale, die diagnostischen Wert haben können.' },
                            { step: '02', title: 'Signale erfasst', desc: 'Nicht-invasive Sensorik zeichnet Pflanzenvitalwerte zusammen mit Klimadaten auf — und erstellt einen synchronisierten, hochauflösenden Datensatz für die Analyse.' },
                            { step: '03', title: 'Intelligenz entsteht', desc: 'Analysen identifizieren Muster, Anomalien und aufkommende Korrelationen — eine fortlaufende wissenschaftliche Untersuchung für prädiktiven Anbau.' },
                            { step: '04', title: 'Anbauer profitieren', desc: 'Datengestützte Erkenntnisse helfen Anbauern, früher und präziser zu reagieren — für bessere Ertragsergebnisse und mehr Nachhaltigkeit.' },
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
                 VERTRAUEN — Social Proof & Autorität
                 1 Botschaft: Warum sollten Sie uns vertrauen?
            ═══════════════════════════════════════════ */}
            <section className="py-28 px-6 bg-apple-gray-100">
                <div className="max-w-[1280px] mx-auto">
                    <ScrollReveal>
                        <div className="text-center mb-16">
                            <p className="text-sm font-semibold text-gm-green-600 uppercase tracking-widest mb-4">
                                Wissenschaftlich fundiert
                            </p>
                            <h2 className="text-3xl md:text-5xl font-bold text-apple-gray-800 mb-4 tracking-tight">
                                Verwurzelt in Forschung. Erprobt im Feld.
                            </h2>
                            <p className="text-lg text-apple-gray-400 max-w-2xl mx-auto">
                                Entwickelt in Zusammenarbeit mit angewandter Agrarforschung an der FHNW —
                                derzeit in realen Gewächshausumgebungen getestet und validiert.
                            </p>
                        </div>
                    </ScrollReveal>

                    <div className="grid md:grid-cols-3 gap-8 max-w-[960px] mx-auto">
                        <ScrollReveal delay={100}>
                            <div className="text-center p-8">
                                <div className="text-4xl md:text-5xl font-bold gradient-text stat-value mb-2">FHNW</div>
                                <p className="text-sm text-apple-gray-400">Fachhochschule<br />Nordwestschweiz</p>
                            </div>
                        </ScrollReveal>
                        <ScrollReveal delay={250}>
                            <div className="text-center p-8">
                                <div className="text-4xl md:text-5xl font-bold gradient-text stat-value mb-2">24/7</div>
                                <p className="text-sm text-apple-gray-400">Kontinuierliche biologische<br />Signalüberwachung</p>
                            </div>
                        </ScrollReveal>
                        <ScrollReveal delay={400}>
                            <div className="text-center p-8">
                                <div className="text-4xl md:text-5xl font-bold gradient-text stat-value mb-2">Brugg</div>
                                <p className="text-sm text-apple-gray-400">Schweizer Präzisionstechnik<br />aus dem Innovationszentrum</p>
                            </div>
                        </ScrollReveal>
                    </div>
                </div>
            </section>

            {/* ═══════════════════════════════════════════
                 VORTEILE — Warum Anbauer uns wählen
                 1 Botschaft: Was sind die Ergebnisse?
            ═══════════════════════════════════════════ */}
            <section className="py-28 px-6">
                <div className="max-w-[1280px] mx-auto">
                    <ScrollReveal>
                        <div className="text-center mb-16">
                            <p className="text-sm font-semibold text-gm-green-600 uppercase tracking-widest mb-4">
                                Vorteile
                            </p>
                            <h2 className="text-3xl md:text-5xl font-bold text-apple-gray-800 mb-4 tracking-tight">
                                Warum Anbauer GreenMind wählen.
                            </h2>
                        </div>
                    </ScrollReveal>
                    <div className="flex hide-scrollbar overflow-x-auto snap-x snap-mandatory gap-4 md:grid md:grid-cols-3 md:gap-6 pb-4 md:pb-0 -mx-6 px-6 md:mx-0 md:px-0">
                        {[
                            { title: 'Gesündere Pflanzen', desc: 'Gewinnen Sie tiefere Einblicke in den Pflanzenzustand — Frühsignale können helfen, einzugreifen, bevor sich Stress in sichtbare Schäden verwandelt.' },
                            { title: 'Höhere Ertragsvorhersagbarkeit', desc: 'Erstellen Sie Ertragsmodelle mithilfe biologischer und umweltbezogener Trenddaten — ein aufkommender Ansatz für eine zuverlässigere Ernteplanung.' },
                            { title: 'Präziser Ressourceneinsatz', desc: 'Treffen Sie Wasser- und Nährstoffentscheidungen auf Basis kontinuierlicher Daten — konzipiert, um Verschwendung durch präziseres Ressourcenmanagement zu reduzieren.' },
                            { title: 'Schnellere Risikoreaktion', desc: 'Frühindikatoren können Krankheitsdruck, Hitzestress oder Bewässerungsprobleme signalisieren — für schnellere Reaktionszeiten.' },
                            { title: 'Nachhaltiger Betrieb', desc: 'Arbeiten Sie auf einen geringeren ökologischen Fussabdruck durch datengestützte Energie-, Wasser- und Inputoptimierung hin.' },
                            { title: 'Klügere Entscheidungen', desc: 'Wechseln Sie von Vermutungen zu kontinuierlicher, wissenschaftsbasierter Intelligenz in Ihrem gesamten Anbaubetrieb.' },
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
                 CTA — Finale Conversion
                 1 Botschaft: Machen Sie den nächsten Schritt.
            ═══════════════════════════════════════════ */}
            <section className="py-28 px-6 bg-apple-gray-800">
                <div className="max-w-[1280px] mx-auto text-center">
                    <ScrollReveal>
                        <p className="text-sm font-semibold text-gm-green-400 uppercase tracking-widest mb-4">
                            Jetzt starten
                        </p>
                    </ScrollReveal>
                    <ScrollReveal delay={150}>
                        <h2 className="text-3xl md:text-5xl font-bold text-white mb-6 tracking-tight leading-tight">
                            Die Zukunft des Anbaus<br className="hidden md:block" />
                            hört zu.
                        </h2>
                    </ScrollReveal>
                    <ScrollReveal delay={300}>
                        <p className="text-lg text-apple-gray-300 mb-10 max-w-xl mx-auto leading-relaxed">
                            Werden Sie Teil der vorausschauenden Anbauer, die nachhaltigere,
                            produktivere und intelligentere Anbaubetriebe aufbauen.
                        </p>
                    </ScrollReveal>
                    <ScrollReveal delay={450}>
                        <Link
                            href="/early-access"
                            className="btn-glow inline-flex px-10 py-4 bg-gm-green-500 text-white rounded-full text-lg font-medium hover:bg-gm-green-400 transition-colors duration-300"
                        >
                            Pflanzensignale überwachen
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
                            <Link href="/product" className="hover:text-apple-gray-800 transition-colors duration-200">Produkt</Link>
                            <Link href="/technology" className="hover:text-apple-gray-800 transition-colors duration-200">Technologie</Link>
                            <Link href="/about" className="hover:text-apple-gray-800 transition-colors duration-200">Über uns</Link>
                            <Link href="/contact" className="hover:text-apple-gray-800 transition-colors duration-200">Kontakt</Link>
                        </div>
                    </div>

                    <div className="mt-8 pt-8 border-t border-apple-gray-200/50 flex flex-col md:flex-row items-center justify-between gap-4">
                        <p className="text-sm text-apple-gray-400">© 2026 GreenMind. Alle Rechte vorbehalten.</p>
                        <p className="text-sm text-apple-gray-300">FHNW Campus Brugg-Windisch · Brugg, Schweiz</p>
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
