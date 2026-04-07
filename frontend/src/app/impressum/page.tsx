'use client';

import ScrollReveal from '@/components/ScrollReveal';

export default function Impressum() {
    return (
        <div className="min-h-screen pt-28 pb-24 px-6 max-w-[800px] mx-auto">
            <ScrollReveal>
                <h1 className="text-4xl md:text-5xl font-bold text-apple-gray-800 mb-8 tracking-tight">Impressum</h1>
            </ScrollReveal>

            <ScrollReveal delay={150}>
                <div className="space-y-8 text-apple-gray-600 leading-relaxed">
                    <section>
                        <h2 className="text-xl font-semibold text-apple-gray-800 mb-4">Kontaktadresse</h2>
                        <p>
                            Galaxyadvisors AG<br />
                            Laurenzenvorstadt 69<br />
                            5000 Aarau
                        </p>
                        <p className="mt-4">
                            <a href="mailto:info@galaxyadvisors.com" className="text-gm-green-600 hover:underline">info@galaxyadvisors.com</a>
                        </p>
                    </section>

                    <section>
                        <h2 className="text-xl font-semibold text-apple-gray-800 mb-4">Vertretungsberechtigte Personen</h2>
                        <p>Verwaltungsrat / Geschäftsleitung der Galaxyadvisors AG</p>
                    </section>

                    <section>
                        <h2 className="text-xl font-semibold text-apple-gray-800 mb-4">Haftungsausschluss</h2>
                        <p>
                            Der Autor übernimmt keinerlei Gewähr hinsichtlich der inhaltlichen Richtigkeit, Genauigkeit, Aktualität, Zuverlässigkeit und Vollständigkeit der Informationen. Haftungsansprüche gegen den Autor wegen Schäden materieller oder immaterieller Art, welche aus dem Zugriff oder der Nutzung bzw. Nichtnutzung der veröffentlichten Informationen, durch Missbrauch der Verbindung oder durch technische Störungen entstanden sind, werden ausgeschlossen.
                        </p>
                    </section>

                    <section>
                        <h2 className="text-xl font-semibold text-apple-gray-800 mb-4">Haftung für Links</h2>
                        <p>
                            Verweise und Links auf Webseiten Dritter liegen ausserhalb unseres Verantwortungsbereichs. Es wird jegliche Verantwortung für solche Webseiten abgelehnt. Der Zugriff und die Nutzung solcher Webseiten erfolgen auf eigene Gefahr des Nutzers oder der Nutzerin.
                        </p>
                    </section>

                    <section>
                        <h2 className="text-xl font-semibold text-apple-gray-800 mb-4">Urheberrechte</h2>
                        <p>
                            Die Urheber- und alle anderen Rechte an Inhalten, Bildern, Fotos oder anderen Dateien auf der Website gehören ausschliesslich der Galaxyadvisors AG oder den speziell genannten Rechtsinhabern. Für die Reproduktion jeglicher Elemente ist die schriftliche Zustimmung der Urheberrechtsträger im Voraus einzuholen.
                        </p>
                    </section>
                </div>
            </ScrollReveal>
        </div>
    );
}
