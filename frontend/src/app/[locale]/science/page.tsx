'use client';

import { usePublicText } from '@/i18n/public-text';

import { useState, useMemo } from 'react';
import ScrollReveal from '@/components/ScrollReveal';
import { RESEARCH_PAPERS, CATEGORY_OPTIONS, ResearchPaper } from '@/lib/research-data';

const CATEGORY_DOTS: Record<string, string> = {
  fundamentals: 'bg-[#3E6A9E]',
  'stress-detection': 'bg-[#B4621E]',
  hardware: 'bg-[#8A7A3A]',
  'predictive-analytics': 'bg-mercury-accent',
};

export default function SciencePage() {
  const tx = usePublicText();
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('all');
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const filtered = useMemo(() => {
    return RESEARCH_PAPERS.filter((p: ResearchPaper) => {
      const matchesCategory = category === 'all' || p.category === category;
      const q = search.toLocaleLowerCase(tx.locale);
      const matchesSearch =
        !q ||
        p.title.toLowerCase().includes(q) ||
        p.authors.toLowerCase().includes(q) ||
        p.journal.toLowerCase().includes(q) ||
        tx(p.greenmindLink).toLocaleLowerCase(tx.locale).includes(q);
      return matchesCategory && matchesSearch;
    });
  }, [search, category, tx]);

  return (
    <div className="min-h-screen bg-mercury-bg text-mercury-ink">
      <div className="pt-32 md:pt-40 pb-16 px-6 max-w-[1200px] mx-auto">
        {/* Hero */}
        <ScrollReveal>
          <div className="flex flex-col gap-5 pb-10 md:pb-14">
            <span className="landing-eyebrow">{tx('Science Center')}</span>
            <h1 className="landing-display m-0 text-[clamp(40px,6vw,76px)] leading-[1.03]">
              {tx('Die Forschung hinter')} GreenMind.
            </h1>
            <p className="m-0 max-w-[640px] text-lg leading-relaxed text-mercury-muted">
              {tx(
                'Unsere Technologie basiert auf peer-reviewed Forschung in der Pflanzenelektrophysiologie. Hier finden Sie die wissenschaftlichen Grundlagen — von bioelektrischen Signalen bis zu datengestützter Stressklassifizierung.'
              )}
            </p>
          </div>
        </ScrollReveal>

        {/* Filters */}
        <ScrollReveal delay={200}>
          <div className="flex flex-col lg:flex-row lg:items-end lg:justify-between gap-4 mb-6">
            <div className="flex-1 max-w-[420px]">
              <label htmlFor="paper-search" className="sr-only">
                {tx('Paper durchsuchen…')}
              </label>
              <input
                id="paper-search"
                type="search"
                placeholder={tx('Paper durchsuchen…')}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-full h-[46px] px-[18px] rounded-full bg-white border border-mercury-field text-mercury-ink text-[15px] placeholder:text-mercury-muted focus:outline-none focus:ring-2 focus:ring-mercury-accent focus:ring-offset-2 focus:ring-offset-mercury-bg"
              />
            </div>

            <div
              role="group"
              className="flex gap-2 overflow-x-auto hide-scrollbar pb-1 lg:flex-wrap"
            >
              {CATEGORY_OPTIONS.map((cat) => {
                const active = category === cat.value;
                return (
                  <button
                    key={cat.value}
                    type="button"
                    aria-pressed={active}
                    onClick={() => setCategory(cat.value)}
                    className={`h-[46px] px-[18px] rounded-full text-sm whitespace-nowrap border transition-colors duration-150 ${
                      active
                        ? 'bg-mercury-accent border-mercury-accent text-white'
                        : 'bg-white border-mercury-field text-mercury-ink hover:border-mercury-muted'
                    }`}
                  >
                    {tx(cat.label)}
                  </button>
                );
              })}
            </div>
          </div>
        </ScrollReveal>

        {/* Results Count */}
        <p className="text-sm text-mercury-muted mb-5" role="status">
          {tx(
            '{count, plural, one {# Veröffentlichung gefunden} other {# Veröffentlichungen gefunden}}',
            { count: filtered.length }
          )}
        </p>

        {/* Paper Grid */}
        {filtered.length === 0 ? (
          <div className="bg-white rounded-[24px] px-6 py-16 text-center flex flex-col gap-2">
            <h3 className="m-0 text-xl font-medium">{tx('Keine Ergebnisse')}</h3>
            <p className="m-0 text-[15px] text-mercury-muted">
              {tx('Passe deine Suche oder Kategorie an, um Paper zu finden.')}
            </p>
          </div>
        ) : (
          <div className="grid gap-5 md:grid-cols-2">
            {filtered.map((paper: ResearchPaper, i: number) => {
              const isExpanded = expandedId === paper.id;

              return (
                <ScrollReveal key={paper.id} delay={i * 80}>
                  <article className="bg-white rounded-[24px] p-6 md:p-[30px] flex flex-col gap-3.5 h-full">
                    <div className="flex items-center justify-between gap-3">
                      <span className="inline-flex items-center gap-2 text-xs font-medium text-mercury-ink/80 bg-mercury-soft rounded-full px-3 py-[5px]">
                        <span
                          aria-hidden="true"
                          className={`w-[7px] h-[7px] rounded-full ${CATEGORY_DOTS[paper.category]}`}
                        />
                        {tx(paper.categoryLabel)}
                      </span>
                      <span className="text-[13px] text-mercury-muted tabular-nums">
                        {paper.year}
                      </span>
                    </div>

                    <h3 className="mt-1 mb-0 text-[19px] leading-snug font-medium tracking-[-0.01em]">
                      {paper.title}
                    </h3>

                    <p className="m-0 text-sm text-mercury-muted">
                      <span className="line-clamp-1">{paper.authors}</span>
                      <span className="text-mercury-accent font-medium">{paper.journal}</span>
                    </p>

                    <div className="bg-mercury-soft rounded-2xl px-[18px] py-4 flex flex-col gap-1.5 flex-1">
                      <span className="text-xs font-semibold text-mercury-accent">
                        {tx('🌱 GreenMind Relevanz').replace(/^\p{Extended_Pictographic}\s*/u, '')}
                      </span>
                      <p
                        className={`m-0 text-sm leading-relaxed text-mercury-ink/80 ${!isExpanded ? 'line-clamp-3' : ''}`}
                      >
                        {tx(paper.greenmindLink)}
                      </p>
                    </div>

                    {isExpanded && (
                      <div className="border border-mercury-line rounded-2xl px-[18px] py-4 flex flex-col gap-1.5">
                        <span className="text-xs font-semibold text-mercury-muted">
                          {tx('Abstract')}
                        </span>
                        <p className="m-0 text-sm leading-relaxed text-mercury-ink/80">
                          {tx(paper.abstract)}
                        </p>
                      </div>
                    )}

                    <div className="flex items-center gap-2.5 mt-auto pt-1">
                      <a
                        href={paper.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex-1 text-center px-[18px] py-3 bg-mercury-accent text-white rounded-full text-sm font-medium hover:bg-mercury-accent-dark transition-colors duration-150"
                      >
                        {tx('Paper lesen →')}
                      </a>
                      <button
                        type="button"
                        aria-expanded={isExpanded}
                        onClick={() => setExpandedId(isExpanded ? null : paper.id)}
                        className="px-5 py-3 bg-white border border-mercury-field text-mercury-ink rounded-full text-sm hover:border-mercury-muted transition-colors"
                      >
                        {isExpanded ? tx('Weniger') : tx('Details')}
                      </button>
                    </div>

                    <p className="m-0 text-xs text-mercury-muted font-mono truncate">
                      {tx('DOI:')} {paper.doi}
                    </p>
                  </article>
                </ScrollReveal>
              );
            })}
          </div>
        )}

        {/* Footer Note */}
        <div className="text-center pt-16 pb-12">
          <p className="m-0 text-sm text-mercury-muted">
            {tx(
              'Wissenschaftliche Veröffentlichungen und gekennzeichnete Preprints; nach Möglichkeit frei zugänglich.'
            )}
          </p>
          <p className="mt-1 mb-0 text-sm text-mercury-muted">
            {tx('Letzte Aktualisierung: Mai 2026')}
          </p>
        </div>
      </div>
    </div>
  );
}
