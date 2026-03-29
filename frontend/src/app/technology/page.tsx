'use client';

import ScrollReveal from '@/components/ScrollReveal';

export default function TechnologyPage() {
    return (
        <div className="min-h-screen">
            <div className="pt-28 pb-24 px-6 max-w-[1280px] mx-auto">
                <ScrollReveal>
                    <p className="text-sm font-semibold text-gm-green-600 uppercase tracking-widest mb-4">Technologie</p>
                    <h1 className="text-4xl md:text-6xl font-bold text-apple-gray-800 mb-6 tracking-tight">Die Wissenschaft<br />hinter GreenMind.</h1>
                </ScrollReveal>
                <ScrollReveal delay={200}>
                    <p className="text-xl text-apple-gray-500 max-w-2xl mb-20 leading-relaxed">
                        Wie wir bioelektrische Pflanzensignale erfassen, verarbeiten und in
                        verwertbare Erkenntnisse für den Anbau umwandeln.
                    </p>
                </ScrollReveal>

                <div className="space-y-24">
                    <ScrollReveal>
                        <section className="max-w-3xl">
                            <h2 className="text-2xl md:text-3xl font-semibold text-apple-gray-800 mb-6">Bioelektrische Signale</h2>
                            <p className="text-apple-gray-500 leading-relaxed mb-4">
                                Pflanzen reagieren auf Veränderungen in ihrer Umgebung mit messbaren
                                elektrischen Signalen — auf Licht, Wasserverfügbarkeit, Temperatur,
                                Nährstoffe und Schädlingsbefall. Wissenschaftliche Studien zeigen, dass
                                diese Signale Stressinformationen enthalten, die herkömmliche Umgebungssensoren
                                nicht liefern können.
                            </p>
                            <p className="text-apple-gray-500 leading-relaxed">
                                GreenMind erfasst diese Signale direkt an der Pflanze — nicht-invasiv und
                                kontinuierlich. In Kombination mit klassischen Umweltdaten wie Temperatur und
                                Bodenfeuchtigkeit entsteht ein umfassendes Bild des Pflanzenzustands.
                            </p>
                        </section>
                    </ScrollReveal>

                    <section>
                        <ScrollReveal>
                            <h2 className="text-2xl md:text-3xl font-semibold text-apple-gray-800 mb-8">Drei Schichten, ein System</h2>
                        </ScrollReveal>
                        <div className="flex hide-scrollbar overflow-x-auto snap-x snap-mandatory gap-4 md:grid md:grid-cols-3 md:gap-6 pb-4 md:pb-0 -mx-6 px-6 md:mx-0 md:px-0">
                            {[
                                {
                                    title: 'Sensorik',
                                    desc: 'Sensoren an der Pflanze erfassen bioelektrische Signale und zeichnen parallel Umgebungsdaten auf — Temperatur, Licht, Luftfeuchtigkeit, Bodenfeuchtigkeit. Alles synchron auf einer gemeinsamen Zeitachse.'
                                },
                                {
                                    title: 'Datenverarbeitung',
                                    desc: 'Rohdaten werden automatisch gefiltert, normalisiert und in analysierbare Zeitreihen aufbereitet. Muster und Abweichungen werden identifiziert — als Basis für die Interpretation.'
                                },
                                {
                                    title: 'Auswertung',
                                    desc: 'Signalmuster werden mit dokumentierten Stressereignissen abgeglichen. So entstehen Erkenntnisse über Zusammenhänge zwischen Pflanzensignalen und realen Bedingungen im Feld.'
                                },
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

                    <ScrollReveal>
                        <section className="max-w-3xl">
                            <h2 className="text-2xl md:text-3xl font-semibold text-apple-gray-800 mb-6">Hardware</h2>
                            <p className="text-apple-gray-500 leading-relaxed mb-4">
                                Das System besteht aus drei Ebenen: ESP32-basierte Sensoreinheiten im Feld
                                erfassen die Daten und übermitteln sie an Raspberry Pi Gateways. Diese speichern
                                lokal und leiten die Daten an den zentralen Server weiter. Lokale Datenhaltung
                                garantiert, dass keine Messwerte verloren gehen — auch bei Verbindungsunterbrüchen.
                            </p>
                            <p className="text-apple-gray-500 leading-relaxed">
                                Die Kommunikation zwischen Feld-Gateways und Server läuft verschlüsselt (HTTPS).
                                Jedes Gateway authentifiziert sich mit einem individuellen API-Schlüssel.
                            </p>
                        </section>
                    </ScrollReveal>
                </div>
            </div>
        </div>
    );
}
