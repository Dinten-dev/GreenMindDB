'use client';

import Link from 'next/link';
import ScrollReveal from '@/components/ScrollReveal';
import { useLocale, useTranslations } from 'next-intl';

/* ── Decorative illustrations (aria-hidden) ──────────── */
function SignalLines() {
  return (
    <svg
      viewBox="0 0 1200 380"
      preserveAspectRatio="xMidYMid slice"
      className="w-full h-full"
      fill="none"
    >
      <path
        d="M0 230 C 150 230, 210 140, 330 160 S 520 260, 640 190 S 820 90, 960 150 S 1120 210, 1200 180"
        stroke="#2E5E3E"
        strokeWidth="2"
        opacity="0.9"
      />
      <path
        d="M0 250 C 160 250, 220 170, 340 185 S 520 275, 650 215 S 830 125, 970 175 S 1130 230, 1200 205"
        stroke="#2E5E3E"
        strokeWidth="1.2"
        opacity="0.45"
      />
      <path
        d="M0 270 C 170 270, 230 200, 350 210 S 530 290, 660 240 S 840 160, 980 200 S 1140 250, 1200 230"
        stroke="#2E5E3E"
        opacity="0.25"
      />
      <path
        d="M0 290 C 180 290, 240 230, 360 235 S 540 305, 670 265 S 850 195, 990 225 S 1150 270, 1200 255"
        stroke="#2E5E3E"
        opacity="0.14"
      />
      <circle cx="330" cy="160" r="6" fill="#2E5E3E" />
      <circle cx="330" cy="160" r="18" stroke="#2E5E3E" opacity="0.25" />
      <circle cx="640" cy="190" r="6" fill="#2E5E3E" />
      <circle cx="960" cy="150" r="6" fill="#2E5E3E" />
      <circle cx="960" cy="150" r="18" stroke="#2E5E3E" opacity="0.25" />
      <circle cx="960" cy="150" r="34" stroke="#2E5E3E" opacity="0.12" />
    </svg>
  );
}

function PulseArt() {
  return (
    <svg width="160" height="120" viewBox="0 0 160 120" fill="none">
      <circle cx="80" cy="60" r="8" fill="#2E5E3E" />
      <circle cx="80" cy="60" r="24" stroke="#2E5E3E" opacity="0.45" />
      <circle cx="80" cy="60" r="40" stroke="#2E5E3E" opacity="0.25" />
      <circle cx="80" cy="60" r="56" stroke="#2E5E3E" opacity="0.12" />
    </svg>
  );
}

function TrendArt() {
  return (
    <svg width="170" height="120" viewBox="0 0 170 120" fill="none">
      <path d="M10 100 L10 20 M10 100 L160 100" stroke="#9AA196" />
      <path d="M20 88 C 50 84, 60 70, 85 64 S 125 40, 155 24" stroke="#2E5E3E" strokeWidth="2" />
      <circle cx="85" cy="64" r="4" fill="#2E5E3E" />
      <circle cx="155" cy="24" r="4" fill="#2E5E3E" />
    </svg>
  );
}

function DropArt() {
  return (
    <svg width="160" height="120" viewBox="0 0 160 120" fill="none">
      <path
        d="M80 18 C 96 42, 108 58, 108 74 A 28 28 0 0 1 52 74 C 52 58, 64 42, 80 18 Z"
        stroke="#2E5E3E"
        strokeWidth="2"
      />
      <path d="M66 76 A 14 14 0 0 0 80 90" stroke="#2E5E3E" opacity="0.5" />
      <path d="M30 106 H130" stroke="#9AA196" strokeDasharray="3 5" />
    </svg>
  );
}

const STATUS_TONES = {
  accent: 'bg-mercury-accent-soft text-mercury-accent',
  amber: 'bg-mercury-amber-soft text-mercury-amber',
  neutral: 'bg-[#ECEDE8] text-[#4E544B]',
} as const;

export default function LandingPage() {
  const t = useTranslations('LandingPage');
  const locale = useLocale();

  const features = [
    { key: 'earlyStress', art: <PulseArt /> },
    { key: 'dataPlanning', art: <TrendArt /> },
    { key: 'resources', art: <DropArt /> },
  ] as const;

  const steps = ['s1', 's2', 's3', 's4'] as const;

  const status = [
    { key: 'foundation', tone: 'accent' },
    { key: 'prototype', tone: 'accent' },
    { key: 'fieldTests', tone: 'amber' },
    { key: 'later', tone: 'neutral' },
  ] as const;

  return (
    <div className="landing-page min-h-screen bg-mercury-bg text-mercury-ink">
      {/* ── Hero ─────────────────────────────── */}
      <section className="max-w-[1200px] mx-auto px-6 pt-36 md:pt-44 pb-16 md:pb-20 flex flex-col items-center text-center gap-7">
        <h1 className="landing-display m-0 text-[clamp(44px,7vw,92px)]">
          {t('hero.titlePart1')}
          <br />
          {t('hero.titlePart2')}
        </h1>
        <p className="m-0 max-w-[620px] text-lg md:text-[19px] leading-relaxed text-mercury-muted">
          {t('hero.subtitle')}
        </p>
        <div className="flex flex-col sm:flex-row flex-wrap justify-center gap-3 mt-2 w-full sm:w-auto">
          <Link href={`/${locale}/early-access`} className="landing-primary">
            {t('hero.requestAccess')}
          </Link>
          <Link href={`/${locale}/technology`} className="landing-secondary">
            {t('hero.howItWorks')}
          </Link>
        </div>
        <p className="m-0 text-[13px] text-mercury-muted">{t('hero.note')}</p>
      </section>

      {/* ── Hero visual ──────────────────────── */}
      <section aria-hidden="true" className="max-w-[1200px] mx-auto px-6 pb-24 md:pb-32">
        <div className="rounded-[28px] bg-mercury-tint h-[220px] md:h-[380px] overflow-hidden">
          <SignalLines />
        </div>
      </section>

      {/* ── Problem ──────────────────────────── */}
      <section className="max-w-[1200px] mx-auto px-6 pb-24 md:pb-36 flex flex-wrap gap-8 md:gap-12 items-start">
        <ScrollReveal className="flex-[1_1_380px]">
          <div className="flex flex-col gap-4">
            <span className="landing-eyebrow">{t('problem.tag')}</span>
            <h2 className="landing-h2 m-0">
              {t('problem.title1')} {t('problem.title2')}
            </h2>
          </div>
        </ScrollReveal>
        <ScrollReveal delay={150} className="flex-[1_1_380px]">
          <p className="m-0 md:pt-9 text-lg leading-relaxed text-mercury-muted">
            {t('problem.description')}
          </p>
        </ScrollReveal>
      </section>

      {/* ── Approach ─────────────────────────── */}
      <section className="max-w-[1200px] mx-auto px-6 pb-24 md:pb-36 flex flex-col gap-12">
        <ScrollReveal>
          <div className="flex flex-col items-center text-center gap-4">
            <span className="landing-eyebrow">{t('solution.tag')}</span>
            <h2 className="landing-h2 m-0 max-w-[760px]">{t('solution.title')}</h2>
            <p className="m-0 max-w-[600px] text-[17px] leading-relaxed text-mercury-muted">
              {t('solution.description')}
            </p>
          </div>
        </ScrollReveal>
        <div className="landing-feature-grid grid grid-cols-1 md:grid-cols-3 gap-5">
          {features.map((feature, i) => (
            <ScrollReveal key={feature.key} delay={100 + i * 150}>
              <article className="bg-white rounded-[24px] p-7 flex flex-col gap-3.5 h-full">
                <div
                  aria-hidden="true"
                  className="h-[180px] rounded-2xl bg-mercury-soft flex items-center justify-center"
                >
                  {feature.art}
                </div>
                <h3 className="mt-1.5 mb-0 text-xl font-medium tracking-[-0.015em]">
                  {t(`solution.features.${feature.key}.title`)}
                </h3>
                <p className="m-0 text-[15px] leading-relaxed text-mercury-muted">
                  {t(`solution.features.${feature.key}.description`)}
                </p>
              </article>
            </ScrollReveal>
          ))}
        </div>
      </section>

      {/* ── How it works ─────────────────────── */}
      <section className="bg-white border-y border-mercury-line">
        <div className="max-w-[1200px] mx-auto px-6 py-20 md:py-28 flex flex-col gap-14">
          <ScrollReveal>
            <div className="flex flex-wrap gap-8 justify-between items-end">
              <div className="flex flex-col gap-4 max-w-[620px]">
                <span className="landing-eyebrow">{t('howItWorks.tag')}</span>
                <h2 className="landing-h2 m-0">{t('howItWorks.title')}</h2>
              </div>
              <p className="m-0 max-w-[420px] text-base leading-relaxed text-mercury-muted">
                {t('howItWorks.description')}
              </p>
            </div>
          </ScrollReveal>
          <ol className="list-none m-0 p-0 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 border-t border-mercury-field">
            {steps.map((step, i) => (
              <li key={step} className="pt-7 pr-6 pb-2">
                <ScrollReveal delay={i * 120}>
                  <div className="flex flex-col gap-2.5">
                    <span className="text-[13px] text-mercury-muted tabular-nums">
                      {String(i + 1).padStart(2, '0')}
                    </span>
                    <h3 className="m-0 text-[19px] font-medium">
                      {t(`howItWorks.steps.${step}.title`)}
                    </h3>
                    <p className="m-0 text-[15px] leading-relaxed text-mercury-muted">
                      {t(`howItWorks.steps.${step}.description`)}
                    </p>
                  </div>
                </ScrollReveal>
              </li>
            ))}
          </ol>
        </div>
      </section>

      {/* ── Where we stand ───────────────────── */}
      <section className="max-w-[1200px] mx-auto px-6 py-24 md:py-36 flex flex-col gap-12">
        <ScrollReveal>
          <div className="flex flex-col items-center text-center gap-4">
            <span className="landing-eyebrow">{t('status.tag')}</span>
            <h2 className="landing-h2 m-0 max-w-[780px]">
              {t('status.title1')}
              <br />
              {t('status.title2')}
            </h2>
            <p className="m-0 max-w-[600px] text-[17px] leading-relaxed text-mercury-muted">
              {t('status.description')}
            </p>
          </div>
        </ScrollReveal>
        <div className="landing-feature-grid grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {status.map((item, i) => (
            <ScrollReveal key={item.key} delay={i * 100}>
              <div className="bg-white rounded-[20px] p-[26px] flex flex-col gap-3 h-full">
                <span className={`status-pill ${STATUS_TONES[item.tone]}`}>
                  {t(`status.items.${item.key}.badge`)}
                </span>
                <h3 className="m-0 text-lg font-medium">{t(`status.items.${item.key}.title`)}</h3>
                <p className="m-0 text-sm leading-relaxed text-mercury-muted">
                  {t(`status.items.${item.key}.description`)}
                </p>
              </div>
            </ScrollReveal>
          ))}
        </div>
      </section>

      {/* ── CTA ──────────────────────────────── */}
      <section className="max-w-[1200px] mx-auto px-6 pb-24 md:pb-32">
        <ScrollReveal>
          <div className="bg-mercury-tint rounded-[28px] px-6 md:px-8 py-16 md:py-[88px] flex flex-col items-center text-center gap-5">
            <span className="landing-eyebrow">{t('cta.tag')}</span>
            <h2 className="landing-h2 m-0 max-w-[760px]">
              {t('cta.title1')} {t('cta.title2')}
            </h2>
            <p className="m-0 max-w-[560px] text-[17px] leading-relaxed text-[#4E544B]">
              {t('cta.description')}
            </p>
            <Link href={`/${locale}/early-access`} className="landing-primary mt-1.5">
              {t('cta.button')}
            </Link>
          </div>
        </ScrollReveal>
      </section>
    </div>
  );
}
