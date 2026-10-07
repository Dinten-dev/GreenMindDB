'use client';

import { useTranslations } from 'next-intl';

import ScrollReveal from '@/components/ScrollReveal';

export default function Impressum() {
  const copy = useTranslations('Content');
  return (
    <div className="min-h-screen pt-28 pb-24 px-6 max-w-[800px] mx-auto">
      <ScrollReveal>
        <h1 className="text-4xl md:text-5xl font-bold text-apple-gray-800 mb-8 tracking-tight">
          {copy('legalNotice')}
        </h1>
      </ScrollReveal>

      <ScrollReveal delay={150}>
        <div className="space-y-8 text-apple-gray-600 leading-relaxed">
          <section>
            <h2 className="text-xl font-semibold text-apple-gray-800 mb-4">
              {copy('contactAddress')}
            </h2>
            <p>
              Galaxyadvisors AG
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
              {copy('authorizedRepresentatives')}
            </h2>
            <p>{copy('boardOfDirectorsManagementOfGalaxyadvisorsAG')}</p>
          </section>

          <section>
            <h2 className="text-xl font-semibold text-apple-gray-800 mb-4">{copy('disclaimer')}</h2>
            <p>{copy('theAuthorProvidesNoWarrantyRegardingTheCorrectness')}</p>
          </section>

          <section>
            <h2 className="text-xl font-semibold text-apple-gray-800 mb-4">
              {copy('liabilityForLinks')}
            </h2>
            <p>{copy('referencesAndLinksToThirdPartyWebsitesAre')}</p>
          </section>

          <section>
            <h2 className="text-xl font-semibold text-apple-gray-800 mb-4">{copy('copyright')}</h2>
            <p>{copy('copyrightAndAllOtherRightsToContentImages')}</p>
          </section>
        </div>
      </ScrollReveal>
    </div>
  );
}
