'use client';

import Link from 'next/link';
import ScrollReveal from '@/components/ScrollReveal';

export default function AboutPage() {
    return (
        <div className="min-h-screen">
            <div className="pt-28 pb-24 px-6 max-w-[1280px] mx-auto">
                <ScrollReveal>
                    <p className="text-sm font-semibold text-gm-green-600 uppercase tracking-widest mb-4">Über uns</p>
                    <h1 className="text-4xl md:text-6xl font-bold text-apple-gray-800 mb-6 tracking-tight">Über GreenMind.</h1>
                </ScrollReveal>
                <ScrollReveal delay={200}>
                    <p className="text-xl text-apple-gray-400 max-w-2xl mb-20 leading-relaxed">
                        Die Intelligenzschicht für modernen Gewächshausanbau aufbauen.
                    </p>
                </ScrollReveal>

                <div className="max-w-3xl space-y-16">
                    <ScrollReveal>
                        <section>
                            <h2 className="text-2xl md:text-3xl font-semibold text-apple-gray-800 mb-4">Unsere Mission</h2>
                            <p className="text-apple-gray-400 leading-relaxed">
                                Die Landwirtschaft ernährt die Welt — doch sie arbeitet mit überraschend wenig Echtzeit-Einblick
                                in die Organismen, die sie anbaut. GreenMind will das ändern.
                                Wir glauben, dass das Verständnis des Pflanzenzustands auf biologischer Ebene das Potenzial hat,
                                den Gewächshausbetrieb grundlegend zu verbessern — durch frühere Interventionen, präziseren
                                Ressourceneinsatz und letztlich gesündere Pflanzen und höhere Erträge.
                            </p>
                        </section>
                    </ScrollReveal>

                    <ScrollReveal>
                        <section>
                            <h2 className="text-2xl md:text-3xl font-semibold text-apple-gray-800 mb-4">Warum es wichtig ist</h2>
                            <p className="text-apple-gray-400 leading-relaxed">
                                Der weltweite Nahrungsmittelbedarf steigt. Wasser und Ackerflächen stehen unter Druck. Klimatische
                                Schwankungen machen traditionelle Anbaumethoden weniger vorhersehbar. Gewächshausanbauer
                                brauchen neue Werkzeuge — nicht nur zur Überwachung der Umgebungsbedingungen, sondern um zu verstehen,
                                wie ihre Kulturen reagieren. GreenMind arbeitet an diesem Verständnis und erforscht,
                                wie biologische Signale in handlungsrelevante Intelligenz für eine nachhaltigere,
                                produktivere und widerstandsfähigere Landwirtschaft umgewandelt werden können.
                            </p>
                        </section>
                    </ScrollReveal>

                    <ScrollReveal>
                        <section>
                            <h2 className="text-2xl md:text-3xl font-semibold text-apple-gray-800 mb-4">Das Team</h2>
                            <p className="text-apple-gray-400 leading-relaxed">
                                Wir sind ein multidisziplinäres Team aus Pflanzenwissenschaftlern, Ingenieuren und Produktdesignern
                                mit Sitz in Basel, Schweiz. Unsere Arbeit basiert auf angewandter Forschung der FHNW
                                (Fachhochschule Nordwestschweiz) und ist getrieben von der tiefen Überzeugung,
                                dass Präzisionslandwirtschaft zugänglicher, intelligenter und wirkungsvoller gestaltet werden kann.
                            </p>
                        </section>
                    </ScrollReveal>

                    <ScrollReveal>
                        <section>
                            <h2 className="text-2xl md:text-3xl font-semibold text-apple-gray-800 mb-4">Nachhaltige Innovation</h2>
                            <p className="text-apple-gray-400 leading-relaxed">
                                Wir arbeiten aktiv mit akademischen Forschungseinrichtungen und kommerziellen Anbauern zusammen,
                                um sicherzustellen, dass unsere Plattform echte Herausforderungen adressiert. Jede Funktion, die wir
                                entwickeln, wird in realen Gewächshausumgebungen getestet. Unser Ziel ist nicht Technologie um ihrer
                                selbst willen — sondern messbare Verbesserungen in Nachhaltigkeit, Produktivität und Pflanzengesundheit.
                            </p>
                        </section>
                    </ScrollReveal>
                </div>
            </div>
        </div>
    );
}
