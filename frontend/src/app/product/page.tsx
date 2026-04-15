'use client';

import ScrollReveal from '@/components/ScrollReveal';

/* ── Professional SVG Icons ──────────────── */
function IconClassification() {
    return (
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M2 12h2l3-9 4 18 4-18 3 9h4" />
        </svg>
    );
}

function IconResilience() {
    return (
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
            <path d="M9 12l2 2 4-4" />
        </svg>
    );
}

function IconPipeline() {
    return (
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 3v18h18" />
            <path d="M7 16l4-6 4 4 5-8" />
        </svg>
    );
}

function IconDashboard() {
    return (
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <rect x="3" y="3" width="7" height="7" rx="1" />
            <rect x="14" y="3" width="7" height="7" rx="1" />
            <rect x="3" y="14" width="7" height="7" rx="1" />
            <rect x="14" y="14" width="7" height="7" rx="1" />
        </svg>
    );
}

function IconExport() {
    return (
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
            <polyline points="7 10 12 15 17 10" />
            <line x1="12" y1="15" x2="12" y2="3" />
        </svg>
    );
}

function IconFleet() {
    return (
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <circle cx="12" cy="12" r="2" />
            <path d="M12 2v4" /><path d="M12 18v4" />
            <path d="M4.93 4.93l2.83 2.83" /><path d="M16.24 16.24l2.83 2.83" />
            <path d="M2 12h4" /><path d="M18 12h4" />
            <path d="M4.93 19.07l2.83-2.83" /><path d="M16.24 7.76l2.83-2.83" />
        </svg>
    );
}

const researchAreas: { title: string; desc: string; icon: React.ReactNode }[] = [
    {
        title: 'Stresserkennung',
        desc: 'Unterscheidung verschiedener Stresstypen anhand bioelektrischer Signale — Dürre, Schädlingsbefall, Nährstoffmangel. Ziel: Automatische Klassifizierung auf Basis gesammelter Langzeitdaten.',
        icon: <IconClassification />,
    },
    {
        title: 'Robuste Hardware',
        desc: 'Sensorik, die unter realen Bedingungen funktioniert — hohe Luftfeuchtigkeit, UV-Einstrahlung, Staub und Temperaturschwankungen von -5 °C bis +50 °C.',
        icon: <IconResilience />,
    },
    {
        title: 'Automatische Datenerfassung',
        desc: 'Skalierbare Pipeline für die kontinuierliche Erfassung, Aufbereitung und Langzeitspeicherung aller Messdaten — für Langzeitstudien und kontrollierte Versuche.',
        icon: <IconPipeline />,
    },
    {
        title: 'Echtzeit-Dashboard',
        desc: 'Visualisierung aller Messdaten mit konfigurierbarer Auflösung — von Rohdaten bis Tagesmittel, mit synchronisierter Darstellung und interaktiver Zeitnavigation.',
        icon: <IconDashboard />,
    },
    {
        title: 'Datenexport',
        desc: 'Strukturierter CSV/ZIP-Export aller Sensordaten für die Weiterverarbeitung in Analyse-Tools — mit definierten Formaten für Reproduzierbarkeit.',
        icon: <IconExport />,
    },
    {
        title: 'Gateway-Verwaltung',
        desc: 'Zentrale Verwaltung aller Messstationen — mit Liveness-Monitoring, Remote-Konfiguration und automatischem Reset bei Hardware-Tausch.',
        icon: <IconFleet />,
    },
];

export default function ProductPage() {
    return (
        <div className="min-h-screen">
            <div className="pt-28 pb-24 px-6 max-w-[1280px] mx-auto">
                <ScrollReveal>
                    <p className="text-sm font-semibold text-gm-green-600 uppercase tracking-widest mb-4">Plattform</p>
                    <h1 className="text-4xl md:text-6xl font-bold text-apple-gray-800 mb-6 tracking-tight">
                        Was GreenMind kann.
                    </h1>
                </ScrollReveal>
                <ScrollReveal delay={200}>
                    <p className="text-xl text-apple-gray-500 max-w-2xl mb-8 leading-relaxed">
                        Von der Datenerfassung im Feld bis zur Analyse im Dashboard — die Bereiche,
                        an denen wir arbeiten.
                    </p>
                </ScrollReveal>
                <ScrollReveal delay={300}>
                    <div className="inline-flex items-center gap-2 px-4 py-2 rounded-full bg-amber-50/80 text-amber-700 text-sm font-medium mb-16 border border-amber-200/40">
                        <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />
                        In Entwicklung — nicht kommerziell verfügbar
                    </div>
                </ScrollReveal>

                <div className="flex hide-scrollbar overflow-x-auto snap-x snap-mandatory gap-4 md:grid md:grid-cols-2 md:gap-8 mb-24 pb-4 md:pb-0 -mx-6 px-6 md:mx-0 md:px-0">
                    {researchAreas.map((f, i) => (
                        <ScrollReveal key={f.title} delay={i * 100} className="w-[85vw] md:w-auto flex-shrink-0 snap-center">
                            <div className="card-hover bg-apple-gray-100 rounded-apple-lg p-8 h-full">
                                <div className="icon-container mb-5 text-gm-green-600">{f.icon}</div>
                                <h3 className="text-xl font-semibold text-apple-gray-800 mb-2">{f.title}</h3>
                                <p className="text-apple-gray-500 leading-relaxed">{f.desc}</p>
                            </div>
                        </ScrollReveal>
                    ))}
                </div>
            </div>
        </div>
    );
}
