'use client';

import ScrollReveal from '@/components/ScrollReveal';

export default function TechnologyPage() {
    return (
        <div className="min-h-screen">
            <div className="pt-28 pb-24 px-6 max-w-[1280px] mx-auto">
                <ScrollReveal>
                    <p className="text-sm font-semibold text-gm-green-600 uppercase tracking-widest mb-4">Technologie</p>
                    <h1 className="text-4xl md:text-6xl font-bold text-apple-gray-800 mb-6 tracking-tight">Die Wissenschaft hinter GreenMind.</h1>
                </ScrollReveal>
                <ScrollReveal delay={200}>
                    <p className="text-xl text-apple-gray-500 max-w-2xl mb-20 leading-relaxed">
                        Gegründet auf Pflanzenelektrophysiologie-Forschung. Entwickelt für moderne Landwirtschaft.
                    </p>
                </ScrollReveal>

                <div className="space-y-24">
                    <ScrollReveal>
                        <section className="max-w-3xl">
                            <h2 className="text-2xl md:text-3xl font-semibold text-apple-gray-800 mb-6">Den Pflanzen zuhören</h2>
                            <p className="text-apple-gray-500 leading-relaxed mb-4">
                                Pflanzen sind nicht passiv. Sie erzeugen kontinuierlich messbare biologische Reaktionen
                                auf ihre Umgebung — als Antwort auf Veränderungen in Licht, Wasserverfügbarkeit, Temperatur,
                                Nährstoffgehalt und Krankheitsdruck. Aktuelle Forschung deutet darauf hin, dass diese Reaktionen
                                wertvolle diagnostische Informationen enthalten, die für Anbauer bisher unsichtbar waren.
                            </p>
                            <p className="text-apple-gray-500 leading-relaxed">
                                GreenMind zielt darauf ab, diese Signale direkt an der Quelle durch nicht-invasive Sensortechnologie
                                zu erfassen — und einen kontinuierlichen, hochauflösenden Datensatz aufzubauen, der die traditionelle
                                Umweltüberwachung ergänzt und neue Wege für wissenschaftliche Untersuchungen eröffnet.
                            </p>
                        </section>
                    </ScrollReveal>

                    <section>
                        <ScrollReveal>
                            <h2 className="text-2xl md:text-3xl font-semibold text-apple-gray-800 mb-8">Vom Signal zur Strategie</h2>
                        </ScrollReveal>
                        <div className="flex hide-scrollbar overflow-x-auto snap-x snap-mandatory gap-4 md:grid md:grid-cols-3 md:gap-6 pb-4 md:pb-0 -mx-6 px-6 md:mx-0 md:px-0">
                            {[
                                { title: 'Biologische Signalerfassung', desc: 'Präzise Sensoreinheiten erfassen gleichzeitig Pflanzenvitalwerte und Umweltvariablen — und erstellen einen synchronisierten, multidimensionalen Datensatz der Gewächshausbedingungen.' },
                                { title: 'Intelligente Verarbeitung', desc: 'Skalierbare Analyseinfrastruktur verarbeitet kontinuierliche Datenströme — identifiziert aufkommende Muster, Anomalien und potenzielle Frühwarnindikatoren.' },
                                { title: 'Aufkommende Anbau-Erkenntnisse', desc: 'Laufende Forschung zielt darauf ab, biologische und umweltbezogene Daten in handlungsrelevante Erkenntnisse zu übersetzen — um Anbauer bei früheren, informierteren Entscheidungen zu unterstützen.' },
                            ].map((item, i) => (
                                <ScrollReveal key={item.title} delay={i * 150} className="w-[85vw] md:w-auto flex-shrink-0 snap-center">
                                    <div className="card-hover bg-apple-gray-100 rounded-apple-lg p-8 h-full">
                                        <h3 className="font-semibold text-apple-gray-800 mb-3">{item.title}</h3>
                                        <p className="text-sm text-apple-gray-500 leading-relaxed">{item.desc}</p>
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
