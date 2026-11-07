'use client';

import { usePublicText } from '@/i18n/public-text';

import ScrollReveal from '@/components/ScrollReveal';

export default function Impressum() {
  const tx = usePublicText();
  return (
    <div className="min-h-screen pt-28 pb-24 px-6 max-w-[800px] mx-auto">
      <ScrollReveal>
        <h1 className="text-4xl md:text-5xl font-bold text-apple-gray-800 mb-8 tracking-tight">
          {tx('Impressum')}
        </h1>
      </ScrollReveal>

      <ScrollReveal delay={150}>
        <div className="space-y-8 text-apple-gray-600 leading-relaxed">
          <section>
            <h2 className="text-xl font-semibold text-apple-gray-800 mb-4">
              {tx('Kontaktadresse')}
            </h2>
            <p>
              GreenMind GmbH
              <br />
              Laurenzenvorstadt 69
              <br />
              5000 Aarau
            </p>
            <p className="mt-4">
              <a
                href="mailto:info@galaxyadvisors.com"
                className="text-gm-green-600 hover:underline"
              >
                info@galaxyadvisors.com
              </a>
            </p>
          </section>

          <section>
            <h2 className="text-xl font-semibold text-apple-gray-800 mb-4">
              {tx('Vertretungsberechtigte Personen')}
            </h2>
            <p>{tx('Verwaltungsrat / Geschäftsleitung der GreenMind GmbH')}</p>
          </section>

          <section>
            <h2 className="text-xl font-semibold text-apple-gray-800 mb-4">
              {tx('Haftungsausschluss')}
            </h2>
            <p>
              {tx(
                'Der Autor übernimmt keinerlei Gewähr hinsichtlich der inhaltlichen Richtigkeit, Genauigkeit, Aktualität, Zuverlässigkeit und Vollständigkeit der Informationen. Haftungsansprüche gegen den Autor wegen Schäden materieller oder immaterieller Art, welche aus dem Zugriff oder der Nutzung bzw. Nichtnutzung der veröffentlichten Informationen, durch Missbrauch der Verbindung oder durch technische Störungen entstanden sind, werden ausgeschlossen.'
              )}
            </p>
          </section>

          <section>
            <h2 className="text-xl font-semibold text-apple-gray-800 mb-4">
              {tx('Haftung für Links')}
            </h2>
            <p>
              {tx(
                'Verweise und Links auf Webseiten Dritter liegen ausserhalb unseres Verantwortungsbereichs. Es wird jegliche Verantwortung für solche Webseiten abgelehnt. Der Zugriff und die Nutzung solcher Webseiten erfolgen auf eigene Gefahr des Nutzers oder der Nutzerin.'
              )}
            </p>
          </section>

          <section>
            <h2 className="text-xl font-semibold text-apple-gray-800 mb-4">
              {tx('Urheberrechte')}
            </h2>
            <p>
              {tx(
                'Die Urheber- und alle anderen Rechte an Inhalten, Bildern, Fotos oder anderen Dateien auf der Website gehören ausschliesslich der GreenMind GmbH oder den speziell genannten Rechtsinhabern. Für die Reproduktion jeglicher Elemente ist die schriftliche Zustimmung der Urheberrechtsträger im Voraus einzuholen.'
              )}
            </p>
          </section>
        </div>
      </ScrollReveal>
    </div>
  );
}
