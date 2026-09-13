'use client';

import Link from 'next/link';
import ScrollReveal from '@/components/ScrollReveal';
import { useLocale, useTranslations } from 'next-intl';

/* ── Inline SVG Icon Components ──────────── */
function IconSignal() {
  return (
    <svg
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M2 12h2l3-9 4 18 4-18 3 9h4" />
    </svg>
  );
}

function IconChart() {
  return (
    <svg
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M3 3v18h18" />
      <path d="M7 16l4-6 4 4 5-8" />
    </svg>
  );
}

function IconLeaf() {
  return (
    <svg
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12 22c-4-4-8-7.4-8-12 0-4.4 3.6-8 8-8s8 3.6 8 8c0 4.6-4 8-8 12z" />
      <path d="M12 10v6" />
      <path d="M9 13l3-3 3 3" />
    </svg>
  );
}

export default function LandingPage() {
  const t = useTranslations('LandingPage');
  const locale = useLocale();

  return (
    <div className="landing-page min-h-screen">
      {/* ═══════════════════════════════════════════
                 HERO
            ═══════════════════════════════════════════ */}
      <section className="landing-hero px-6">
        <div className="landing-hero-image" aria-hidden="true" />
        <div className="landing-hero-shade" aria-hidden="true" />
        <div className="landing-hero-content mx-auto text-center">
          <div>
            <p className="inline-flex items-center gap-2 text-sm text-apple-gray-600 mb-7">
              <span className="w-2 h-2 rounded-full bg-gm-green-600" aria-hidden="true" />
              {t('hero.inDevelopment')}
            </p>
            <h1 className="landing-title text-apple-gray-800 mb-7">
              {t('hero.titlePart1')}
              <br />
              <span className="text-gm-green-600">{t('hero.titlePart2')}</span>
            </h1>
            <p className="text-base sm:text-lg md:text-xl text-apple-gray-600 max-w-2xl mx-auto leading-relaxed mb-9">
              {t('hero.subtitle')}
            </p>
            <div className="flex flex-col sm:flex-row sm:items-center justify-center gap-3 sm:gap-5">
              <Link href={`/${locale}/early-access`} className="landing-primary text-center">
                {t('hero.requestAccess')}
              </Link>
              <Link href={`/${locale}/technology`} className="landing-secondary text-center">
                {t('hero.howItWorks')}
              </Link>
            </div>
          </div>
        </div>
      </section>

      {/* ═══════════════════════════════════════════
                 PROBLEM
            ═══════════════════════════════════════════ */}
      <section className="py-16 md:py-28 px-6">
        <div className="max-w-[960px] mx-auto text-center">
          <ScrollReveal>
            <p className="text-sm font-semibold text-gm-green-600 uppercase tracking-widest mb-4">
              {t('problem.tag')}
            </p>
          </ScrollReveal>
          <ScrollReveal delay={150}>
            <h2 className="text-3xl md:text-5xl font-bold text-apple-gray-800 tracking-tight leading-tight mb-6">
              {t('problem.title1')}
              <br className="hidden md:block" />
              <span className="text-apple-gray-500">{t('problem.title2')}</span>
            </h2>
          </ScrollReveal>
          <ScrollReveal delay={300}>
            <p className="text-lg text-apple-gray-500 max-w-2xl mx-auto leading-relaxed">
              {t('problem.description')}
            </p>
          </ScrollReveal>
        </div>
      </section>

      {/* ═══════════════════════════════════════════
                 LÖSUNG
            ═══════════════════════════════════════════ */}
      <section className="py-16 md:py-28 px-6 bg-apple-gray-100">
        <div className="max-w-[1200px] mx-auto">
          <ScrollReveal>
            <div className="text-center mb-10 md:mb-16">
              <p className="text-sm font-semibold text-gm-green-600 uppercase tracking-widest mb-4">
                {t('solution.tag')}
              </p>
              <h2 className="text-3xl md:text-5xl font-bold text-apple-gray-800 mb-4 tracking-tight">
                {t('solution.title')}
              </h2>
              <p className="text-lg text-apple-gray-500 max-w-xl mx-auto">
                {t('solution.description')}
              </p>
            </div>
          </ScrollReveal>
          <div className="landing-feature-grid grid md:grid-cols-3 gap-4 md:gap-6">
            <ScrollReveal delay={100}>
              <FeatureCard
                icon={<IconSignal />}
                title={t('solution.features.earlyStress.title')}
                description={t('solution.features.earlyStress.description')}
              />
            </ScrollReveal>
            <ScrollReveal delay={250}>
              <FeatureCard
                icon={<IconChart />}
                title={t('solution.features.dataPlanning.title')}
                description={t('solution.features.dataPlanning.description')}
              />
            </ScrollReveal>
            <ScrollReveal delay={400}>
              <FeatureCard
                icon={<IconLeaf />}
                title={t('solution.features.resources.title')}
                description={t('solution.features.resources.description')}
              />
            </ScrollReveal>
          </div>
        </div>
      </section>

      {/* ═══════════════════════════════════════════
                 WIE ES FUNKTIONIERT
            ═══════════════════════════════════════════ */}
      <section className="py-16 md:py-28 px-6">
        <div className="max-w-[1200px] mx-auto">
          <ScrollReveal>
            <div className="text-center mb-10 md:mb-20">
              <p className="text-sm font-semibold text-gm-green-600 uppercase tracking-widest mb-4">
                {t('howItWorks.tag')}
              </p>
              <h2 className="text-3xl md:text-5xl font-bold text-apple-gray-800 mb-4 tracking-tight">
                {t('howItWorks.title')}
              </h2>
              <p className="text-lg text-apple-gray-500 max-w-2xl mx-auto">
                {t('howItWorks.description')}
              </p>
            </div>
          </ScrollReveal>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-8">
            {[
              {
                step: '01',
                title: t('howItWorks.steps.s1.title'),
                desc: t('howItWorks.steps.s1.description'),
              },
              {
                step: '02',
                title: t('howItWorks.steps.s2.title'),
                desc: t('howItWorks.steps.s2.description'),
              },
              {
                step: '03',
                title: t('howItWorks.steps.s3.title'),
                desc: t('howItWorks.steps.s3.description'),
              },
              {
                step: '04',
                title: t('howItWorks.steps.s4.title'),
                desc: t('howItWorks.steps.s4.description'),
              },
            ].map((item, i) => (
              <ScrollReveal key={item.step} delay={i * 150}>
                <div className="timeline-step text-center">
                  <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-gm-green-50 mb-5">
                    <span className="text-2xl font-bold gradient-text">{item.step}</span>
                  </div>
                  <h3 className="text-lg font-semibold text-apple-gray-800 mb-2">{item.title}</h3>
                  <p className="text-sm text-apple-gray-500 leading-relaxed">{item.desc}</p>
                </div>
              </ScrollReveal>
            ))}
          </div>
        </div>
      </section>

      {/* ═══════════════════════════════════════════
                 FUNDAMENT
            ═══════════════════════════════════════════ */}
      <section className="py-16 md:py-28 px-6 bg-apple-gray-100">
        <div className="max-w-[1200px] mx-auto">
          <ScrollReveal>
            <div className="text-center mb-10 md:mb-16">
              <p className="text-sm font-semibold text-gm-green-600 uppercase tracking-widest mb-4">
                {t('foundation.tag')}
              </p>
              <h2 className="text-3xl md:text-5xl font-bold text-apple-gray-800 mb-4 tracking-tight">
                {t('foundation.title')}
              </h2>
              <p className="text-lg text-apple-gray-500 max-w-2xl mx-auto">
                {t('foundation.description')}
              </p>
            </div>
          </ScrollReveal>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 md:gap-8 max-w-[960px] mx-auto">
            <ScrollReveal delay={100}>
              <div className="text-center p-4 md:p-8">
                <div className="text-2xl md:text-5xl font-bold gradient-text stat-value mb-2">
                  FHNW
                </div>
                <p
                  className="text-sm text-apple-gray-500"
                  dangerouslySetInnerHTML={{ __html: t('foundation.f1').replace(' ', '<br />') }}
                />
              </div>
            </ScrollReveal>
            <ScrollReveal delay={250}>
              <div className="text-center p-4 md:p-8">
                <div className="text-2xl md:text-5xl font-bold gradient-text stat-value mb-2">
                  24/7
                </div>
                <p
                  className="text-sm text-apple-gray-500"
                  dangerouslySetInnerHTML={{ __html: t('foundation.f2').replace(' ', '<br />') }}
                />
              </div>
            </ScrollReveal>
            <ScrollReveal delay={400}>
              <div className="text-center p-4 md:p-8">
                <div className="text-2xl md:text-5xl font-bold gradient-text stat-value mb-2">
                  Aarau
                </div>
                <p
                  className="text-sm text-apple-gray-500"
                  dangerouslySetInnerHTML={{ __html: t('foundation.f3').replace(' ', '<br />') }}
                />
              </div>
            </ScrollReveal>
          </div>
        </div>
      </section>

      {/* ═══════════════════════════════════════════
                 POTENZIALE
            ═══════════════════════════════════════════ */}
      <section className="py-16 md:py-28 px-6">
        <div className="max-w-[1200px] mx-auto">
          <ScrollReveal>
            <div className="text-center mb-10 md:mb-16">
              <p className="text-sm font-semibold text-gm-green-600 uppercase tracking-widest mb-4">
                {t('potentials.tag')}
              </p>
              <h2 className="text-3xl md:text-5xl font-bold text-apple-gray-800 mb-4 tracking-tight">
                {t('potentials.title')}
              </h2>
            </div>
          </ScrollReveal>
          <div className="landing-feature-grid grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 md:gap-6">
            {[
              { title: t('potentials.items.i1.title'), desc: t('potentials.items.i1.description') },
              { title: t('potentials.items.i2.title'), desc: t('potentials.items.i2.description') },
              { title: t('potentials.items.i3.title'), desc: t('potentials.items.i3.description') },
              { title: t('potentials.items.i4.title'), desc: t('potentials.items.i4.description') },
              { title: t('potentials.items.i5.title'), desc: t('potentials.items.i5.description') },
              { title: t('potentials.items.i6.title'), desc: t('potentials.items.i6.description') },
            ].map((b, i) => (
              <ScrollReveal key={b.title} delay={i * 80}>
                <div className="card-hover bg-white rounded-apple-lg p-6 shadow-apple-card h-full flex flex-col justify-between">
                  <div>
                    <h3 className="text-base font-semibold text-apple-gray-800 mb-2">{b.title}</h3>
                    <p className="text-sm text-apple-gray-500 leading-relaxed">{b.desc}</p>
                  </div>
                </div>
              </ScrollReveal>
            ))}
          </div>
        </div>
      </section>

      {/* ═══════════════════════════════════════════
                 CTA
            ═══════════════════════════════════════════ */}
      <section className="py-16 md:py-28 px-6 bg-apple-gray-800">
        <div className="max-w-[1200px] mx-auto text-center">
          <ScrollReveal>
            <p className="text-sm font-semibold text-gm-green-400 uppercase tracking-widest mb-4">
              {t('cta.tag')}
            </p>
          </ScrollReveal>
          <ScrollReveal delay={150}>
            <h2 className="text-3xl md:text-5xl font-bold text-white mb-6 tracking-tight leading-tight">
              {t('cta.title1')}
              <br className="hidden md:block" />
              {t('cta.title2')}
            </h2>
          </ScrollReveal>
          <ScrollReveal delay={300}>
            <p className="text-lg text-apple-gray-300 mb-10 max-w-xl mx-auto leading-relaxed">
              {t('cta.description')}
            </p>
          </ScrollReveal>
          <ScrollReveal delay={450}>
            <Link
              href={`/${locale}/early-access`}
              className="btn-glow inline-flex w-full md:w-auto justify-center px-10 py-4 bg-gm-green-500 text-white rounded-full text-lg font-medium hover:bg-gm-green-400 transition-colors duration-300"
            >
              {t('cta.button')}
            </Link>
          </ScrollReveal>
        </div>
      </section>
    </div>
  );
}

function FeatureCard({
  icon,
  title,
  description,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
}) {
  return (
    <div className="card-hover bg-white rounded-apple-lg p-5 md:p-8 shadow-apple-card h-full">
      <div className="icon-container mb-5 text-gm-green-600">{icon}</div>
      <h3 className="text-lg font-semibold text-apple-gray-800 mb-2">{title}</h3>
      <p className="text-sm text-apple-gray-500 leading-relaxed">{description}</p>
    </div>
  );
}
