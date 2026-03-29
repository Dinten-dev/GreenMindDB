'use client';

import Image from 'next/image';
import ScrollReveal from '@/components/ScrollReveal';

const team = [
    {
        name: 'Prof. Dr. Peter Gloor',
        role: 'Wissenschaftliche Leitung & Forschung',
        image: '/team/peter-gloor.webp',
        imagePosition: 'center 25%',
        linkedin: 'https://www.linkedin.com/in/petergloor/',
        bio: 'Peter war über zwei Jahrzehnte Research Scientist am MIT Sloan School of Management und ist Honorarprofessor an der Universität Köln. Sein aktueller Forschungsschwerpunkt bei Galaxylabs.org liegt auf Wohlbefinden, Mensch-Tier- und Mensch-Pflanze-Kommunikation. Mit einem PhD in Informatik der Universität Zürich und Post-Doc am MIT bringt er einzigartige Expertise in Collaborative Intelligence und datengetriebener Forschung in das GreenMind-Projekt ein.',
    },
    {
        name: 'Traver Dinten',
        role: 'Technische Entwicklung & Data Science',
        image: '/team/traver-dinten.jpg',
        imagePosition: 'center 10%',
        linkedin: 'https://www.linkedin.com/in/traver-dinten-039532276/',
        bio: 'Traver studiert Data Science an der FHNW und verbindet sein Studium mit praktischer Forschungsarbeit bei GreenMind. Mit einer abgeschlossenen Ausbildung als Physiklaborant EFZ bei armasuisse und Erfahrung in Datenanalyse, Elektronik und Sensorik bildet er die technische Brücke zwischen Hardware-Sensorik und intelligenter Datenauswertung. Er verantwortet die Plattformentwicklung, Sensorintegration und Daten-Pipeline.'
    },
];

function LinkedInIcon() {
    return (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
            <path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.064 2.064 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z" />
        </svg>
    );
}

export default function AboutPage() {
    return (
        <div className="min-h-screen">
            <div className="pt-28 pb-24 px-6 max-w-[1280px] mx-auto">
                <ScrollReveal>
                    <p className="text-sm font-semibold text-gm-green-600 uppercase tracking-widest mb-4">Über uns</p>
                    <h1 className="text-4xl md:text-6xl font-bold text-apple-gray-800 mb-6 tracking-tight">Über GreenMind.</h1>
                </ScrollReveal>
                <ScrollReveal delay={200}>
                    <p className="text-xl text-apple-gray-500 max-w-2xl mb-20 leading-relaxed">
                        Bioelektrische Pflanzensignale verstehen, um den Anbau gezielt zu verbessern.
                    </p>
                </ScrollReveal>

                <div className="max-w-3xl space-y-16">
                    <ScrollReveal>
                        <section>
                            <h2 className="text-2xl md:text-3xl font-semibold text-apple-gray-800 mb-4">Unsere Mission</h2>
                            <p className="text-apple-gray-500 leading-relaxed">
                                Die Landwirtschaft überwacht heute Temperatur, Licht und Bodenfeuchtigkeit —
                                aber nicht den Zustand der Pflanze selbst. GreenMind will das ändern.
                                Wir entwickeln ein System, das bioelektrische Pflanzensignale erfasst und
                                für bessere Anbauentscheidungen nutzbar macht.
                                Ein Projekt der Galaxyadvisors AG in Zusammenarbeit mit der FHNW.
                            </p>
                        </section>
                    </ScrollReveal>

                    <ScrollReveal>
                        <section>
                            <h2 className="text-2xl md:text-3xl font-semibold text-apple-gray-800 mb-4">Warum es wichtig ist</h2>
                            <p className="text-apple-gray-500 leading-relaxed">
                                Der weltweite Nahrungsmittelbedarf steigt. Wasser und Anbauflächen
                                stehen unter Druck. Klimatische Schwankungen machen konventionelle
                                Anbaumethoden weniger vorhersehbar. Pflanzen senden dabei ständig
                                elektrische Signale, die auf Stress hinweisen — oft bevor äussere
                                Schäden sichtbar werden. GreenMind untersucht, ob diese Signale
                                als Grundlage für bessere Entscheidungen im Anbau dienen können.
                            </p>
                        </section>
                    </ScrollReveal>

                    {/* ── Team-Sektion ─────────────────────────── */}
                    <ScrollReveal>
                        <section>
                            <h2 className="text-2xl md:text-3xl font-semibold text-apple-gray-800 mb-8">Das Team</h2>
                            <div className="space-y-10">
                                {team.map((member, i) => (
                                    <ScrollReveal key={member.name} delay={i * 150}>
                                        <div className="flex flex-col sm:flex-row gap-6 items-start bg-apple-gray-50 rounded-apple-lg p-6 sm:p-8 border border-apple-gray-200/50">
                                            <div className="flex-shrink-0">
                                                <Image
                                                    src={member.image}
                                                    alt={member.name}
                                                    width={120}
                                                    height={120}
                                                    className="rounded-full object-cover w-[100px] h-[100px] sm:w-[120px] sm:h-[120px] border-2 border-white shadow-apple"
                                                    style={{ objectPosition: member.imagePosition }}
                                                />
                                            </div>
                                            <div className="flex-1 min-w-0">
                                                <div className="flex items-center gap-3 mb-1">
                                                    <h3 className="text-lg font-semibold text-apple-gray-800">{member.name}</h3>
                                                    <a
                                                        href={member.linkedin}
                                                        target="_blank"
                                                        rel="noopener noreferrer"
                                                        className="text-apple-gray-400 hover:text-[#0A66C2] transition-colors"
                                                        aria-label={`${member.name} auf LinkedIn`}
                                                    >
                                                        <LinkedInIcon />
                                                    </a>
                                                </div>
                                                <p className="text-sm font-medium text-gm-green-600 mb-3">{member.role}</p>
                                                <p className="text-sm text-apple-gray-500 leading-relaxed">{member.bio}</p>
                                            </div>
                                        </div>
                                    </ScrollReveal>
                                ))}
                            </div>
                        </section>
                    </ScrollReveal>

                    <ScrollReveal>
                        <section>
                            <h2 className="text-2xl md:text-3xl font-semibold text-apple-gray-800 mb-4">Wie wir arbeiten</h2>
                            <p className="text-apple-gray-500 leading-relaxed">
                                Jede Komponente wird unter realen Anbaubedingungen getestet —
                                in Gewächshäusern und im Freiland, gemeinsam mit der FHNW
                                und landwirtschaftlichen Partnerbetrieben. Unsere Ergebnisse
                                dokumentieren wir im Science Center und gleichen sie mit
                                aktueller wissenschaftlicher Literatur ab.
                            </p>
                        </section>
                    </ScrollReveal>
                </div>
            </div>
        </div>
    );
}
