'use client';

import { useTranslations } from 'next-intl';

import ScrollReveal from '@/components/ScrollReveal';

export default function TechnologyPage() {
  const copy = useTranslations('Content');
  return (
    <div className="min-h-screen">
      <div className="pt-20 pb-16 md:pt-28 md:pb-24 px-6 max-w-[1280px] mx-auto">
        <ScrollReveal>
          <p className="text-sm font-semibold text-gm-green-600 uppercase tracking-widest mb-4">
            {copy('technology')}
          </p>
          <h1 className="text-4xl md:text-6xl font-bold text-apple-gray-800 mb-6 tracking-tight">
            {copy('theScience')}
            <br />
            {copy('behindGreenMind')}
          </h1>
        </ScrollReveal>
        <ScrollReveal delay={200}>
          <p className="text-xl text-apple-gray-500 max-w-2xl mb-12 md:mb-20 leading-relaxed">
            {copy('howWeCaptureAndProcessPlantBioelectricalSignals')}
          </p>
        </ScrollReveal>

        <div className="space-y-14 md:space-y-24">
          <ScrollReveal>
            <section className="max-w-3xl">
              <h2 className="text-2xl md:text-3xl font-semibold text-apple-gray-800 mb-6">
                {copy('bioelectricalSignals')}
              </h2>
              <p className="text-apple-gray-500 leading-relaxed mb-4">
                {copy('plantsRespondToChangesInTheirEnvironmentWith')}
              </p>
              <p className="text-apple-gray-500 leading-relaxed">
                {copy('greenmindRecordsTheseSignalsDirectlyFromThePlant')}
              </p>
            </section>
          </ScrollReveal>

          <section>
            <ScrollReveal>
              <h2 className="text-2xl md:text-3xl font-semibold text-apple-gray-800 mb-8">
                {copy('threeLayersOneSystem')}
              </h2>
            </ScrollReveal>
            <div className="flex flex-col gap-4 md:grid md:grid-cols-3 md:gap-6">
              {[
                {
                  title: copy('sensing'),
                  desc: copy('sensorsAttachedToThePlantCaptureBioelectricalSignals'),
                },
                {
                  title: copy('dataProcessing'),
                  desc: copy('rawDataIsAutomaticallyFilteredNormalizedAndPrepared'),
                },
                {
                  title: copy('analysis'),
                  desc: copy('signalPatternsAreComparedWithDocumentedStressEvents'),
                },
              ].map((item, i) => (
                <ScrollReveal key={item.title} delay={i * 150}>
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
              <h2 className="text-2xl md:text-3xl font-semibold text-apple-gray-800 mb-6">
                {copy('hardware')}
              </h2>
              <p className="text-apple-gray-500 leading-relaxed mb-4">
                {copy('theSystemHasThreeLayersESP32BasedSensor')}
              </p>
              <p className="text-apple-gray-500 leading-relaxed">
                {copy('communicationBetweenFieldGatewaysAndTheServerIs')}
              </p>
            </section>
          </ScrollReveal>
        </div>
      </div>
    </div>
  );
}
