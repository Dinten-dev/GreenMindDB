'use client';

import { useTranslations } from 'next-intl';

import { useState, useMemo } from 'react';
import ScrollReveal from '@/components/ScrollReveal';
import { RESEARCH_PAPERS, CATEGORY_OPTIONS, ResearchPaper } from '@/lib/research-data';

const researchKeys: Record<string, string> = {
  'Hardware & Sensorik': 'text0',
  'Flexible organische Multielectrode-Arrays kartieren Aktionspotenziale der Venusfliegenfalle mit hoher Auflösung. Die Studie beweist, dass sich bioelektrische Pflanzensignale präzise und nicht-invasiv messen lassen — die technische Grundlage für GreenMinds Sensorik.':
    'text1',
  'Entwicklung konformer organischer Transistor-Arrays (OECTs) für die grossflächige Kartierung von Aktionspotenzialen in Pflanzengewebe mit hoher räumlicher und zeitlicher Auflösung.':
    'text2',
  'Prädiktive Analytik': 'text3',
  'Peter Gloor zeigt, dass eine Purple-Heart-Pflanze via bioelektrischer Signale Umgebungszustände (85,4 % Genauigkeit) und menschliche Emotionen (73 %) klassifizieren kann. Dies belegt, dass Pflanzensignale — wie GreenMind sie erfasst — mit ML hochpräzis auswertbar sind.':
    'text4',
  'Studie zur Klassifikation von Umgebungs- und Emotionszuständen mittels bioelektrischer Signale einer Tradescantia pallida, erfasst mit AD8232-Sensor und ausgewertet mit CNN-Modellen.':
    'text5',
  Grundlagen: 'text6',
  'Peter Gloor weist nach, dass Pflanzen messbare bioelektrische Unterschiede zeigen, wenn sich ein Mensch in der Nähe bewegt. Random-Forest-Klassifikatoren erreichen 62,7 % Genauigkeit bei 2978 Proben über drei Pflanzenarten — ein weiterer Beleg, dass bioelektrische Pflanzensignale auf Umgebungsveränderungen reagieren.':
    'text7',
  'Bioelektrische Aufzeichnungen von 2978 Pflanzenproben (Basilikum, Salat, Tomate) werden mit Random Forest und CNN klassifiziert, um statistisch signifikante Unterschiede zwischen Aufnahmen mit und ohne menschliche Bewegung in der Nähe zu detektieren.':
    'text8',
  'Stress-Detektion': 'text9',
  'Elektrische Impedanzmessungen am Pflanzenstamm erkennen Wasserstress mit bis zu 95 % Korrelation zur Bodenfeuchtigkeit — eine kostengünstige Methode, die Pflanzenbedürfnisse direkt zu erfassen, genau wie GreenMinds Sensor-Ansatz es vorsieht.':
    'text10',
  'In-vivo Impedanzmessungen am Pflanzenstamm zeigen eine signifikante Korrelation mit Umweltparametern. Die Methode ermöglicht eine direkte, kostengünstige Echtzeit-Überwachung des Pflanzenzustands ohne visuelle Inspektion.':
    'text11',
  'Elektrophysiologische Signale werden eingesetzt, um Nährstoffmängel in Gewächshauspflanzen zu erkennen. Dies zeigt, dass GreenMinds Sensortechnologie nicht nur Wasserstress, sondern auch Nährstoffdefizite direkt an der Pflanze messen kann.':
    'text12',
  'Einsatz elektrophysiologischer Signale zur fortgeschrittenen Bewertung von Nährstoffmängeln in Gewächshausumgebungen.':
    'text13',
  'Diese grundlegende Arbeit zeigt, dass Pflanzen über Aktions- und Variationspotenziale auf Umweltstress reagieren — von Schädlingsbefall über Pestizide bis zu Schwermetallen. Sie bildet die wissenschaftliche Basis für die Nutzung von Pflanzen als lebende Biosensoren, wie GreenMind es umsetzt.':
    'text14',
  'Übersicht über die Nutzung von Pflanzen als schnelle Biosensoren für die Erkennung von Lichtrichtung, Insektenangriffen, Pestiziden, Entkopplern und Schwermetallbelastungen anhand elektrischer Signale.':
    'text15',
  'Umfassende Übersicht über Methoden zur Schwermetalldetektion in Pflanzen, einschliesslich nicht-invasiver Messtechniken. Für GreenMind relevant, da Schwermetallbelastung pflanzliche Biomarker und elektrische Signale beeinflusst.':
    'text16',
  'Systematische Übersicht der Analyse- und Detektionstechniken für Schwermetallkonzentrationen in Pflanzen, von ICP-MS über XRF bis zu nicht-invasiven Mikro-Testverfahren und Omics-Ansätzen.':
    'text17',
  'Hedera helix wird mit einem PhytoNode-Wearable ausgestattet, das über 5 Monate elektrophysiologische Signale im Freien erfasst. AutoML-Klassifikatoren erreichen bis zu 95 % F1-Score — ein Beweis, dass pflanzliche Elektrosignale auch unter realen Bedingungen für präzises Umweltmonitoring nutzbar sind.':
    'text18',
  'Biohybrides System mit pflanzentragbarem Gerät (PhytoNode) zur kontinuierlichen Erfassung elektrophysiologischer Aktivität von Hedera helix. AutoML-Ansätze übertreffen manuelles Tuning mit bis zu 95 % F1-Score.':
    'text19',
  'ML-Klassifikatoren (Random Forest, SVC) unterscheiden Reifestadien von Tomatenfrüchten anhand elektrophysiologischer Signale mit über 80 % Genauigkeit. Die Studie belegt, dass das pflanzliche Elektrom — wie GreenMind es erfasst — auch Entwicklungsprozesse in Früchten zuverlässig abbilden kann.':
    'text20',
  'Elektrische Aktivität von Tomatenfrüchten (Solanum lycopersicum var. cerasiforme) wird während der Reifung als Spannungsvariationen erfasst und mittels FFT, Wavelet-Transformation, PSD und Approximate Entropy analysiert. ML-Techniken (Random Forest, SVC, KNN u. a.) klassifizieren drei Reifestadien (Mature Green, Breaker, Light Red) mit bis zu 80,76 % Genauigkeit.':
    'text21',
  'Alle Kategorien': 'text22',
};

const CATEGORY_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  fundamentals: { bg: 'bg-blue-50', text: 'text-blue-700', border: 'border-blue-200/60' },
  'stress-detection': { bg: 'bg-red-50', text: 'text-red-700', border: 'border-red-200/60' },
  hardware: { bg: 'bg-amber-50', text: 'text-amber-700', border: 'border-amber-200/60' },
  'predictive-analytics': {
    bg: 'bg-violet-50',
    text: 'text-violet-700',
    border: 'border-violet-200/60',
  },
};

export default function SciencePage() {
  const copy = useTranslations('Content');
  const research = useTranslations('Research');
  const papers = useMemo(
    () =>
      RESEARCH_PAPERS.map((paper) => ({
        ...paper,
        categoryLabel: research(researchKeys[paper.categoryLabel]),
        greenmindLink: research(researchKeys[paper.greenmindLink]),
        abstract: research(researchKeys[paper.abstract]),
      })),
    [research]
  );
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('all');
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const filtered = useMemo(() => {
    return papers.filter((p: ResearchPaper) => {
      const matchesCategory = category === 'all' || p.category === category;
      const q = search.toLowerCase();
      const matchesSearch =
        !q ||
        p.title.toLowerCase().includes(q) ||
        p.authors.toLowerCase().includes(q) ||
        p.journal.toLowerCase().includes(q) ||
        p.greenmindLink.toLowerCase().includes(q);
      return matchesCategory && matchesSearch;
    });
  }, [search, category, papers]);

  return (
    <div className="min-h-screen">
      <div className="pt-20 pb-16 md:pt-28 md:pb-24 px-6 max-w-[1280px] mx-auto">
        {/* Hero */}
        <ScrollReveal>
          <p className="text-sm font-semibold text-gm-green-600 uppercase tracking-widest mb-4">
            {copy('scienceCenter')}
          </p>
          <h1 className="text-4xl md:text-6xl font-bold text-apple-gray-800 mb-6 tracking-tight">
            {copy('theResearchBehind')}
            <br />
            <span className="bg-gradient-to-r from-gm-green-600 to-gm-green-400 bg-clip-text text-transparent">
              GreenMind.
            </span>
          </h1>
        </ScrollReveal>
        <ScrollReveal delay={200}>
          <p className="text-xl text-apple-gray-500 max-w-2xl mb-10 md:mb-16 leading-relaxed">
            {copy('ourTechnologyBuildsOnPeerReviewedResearchIn')}
          </p>
        </ScrollReveal>

        {/* Filters */}
        <ScrollReveal delay={350}>
          <div className="flex flex-col sm:flex-row gap-4 mb-8">
            {/* Search Bar */}
            <div className="relative flex-1 max-w-md">
              <svg
                className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-apple-gray-400"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
                />
              </svg>
              <input
                type="text"
                placeholder={copy('searchPapers')}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-full pl-10 pr-4 py-3 rounded-apple bg-apple-gray-50 border border-apple-gray-200 text-apple-gray-800 text-sm focus:outline-none focus:ring-2 focus:ring-gm-green-500 focus:bg-white transition-all shadow-sm"
              />
            </div>

            {/* Category Chips */}
            <div className="flex gap-2 overflow-x-auto hide-scrollbar pb-1">
              {CATEGORY_OPTIONS.map((cat) => (
                <button
                  key={cat.value}
                  onClick={() => setCategory(cat.value)}
                  className={`px-4 py-2.5 rounded-full text-sm font-medium whitespace-nowrap transition-all duration-200 ${
                    category === cat.value
                      ? 'bg-gm-green-500 text-white shadow-sm shadow-gm-green-500/20'
                      : 'bg-apple-gray-100 text-apple-gray-500 hover:bg-apple-gray-200'
                  }`}
                >
                  {research(researchKeys[cat.label])}
                </button>
              ))}
            </div>
          </div>
        </ScrollReveal>

        {/* Results Count */}
        <p className="text-sm text-apple-gray-500 mb-6">
          {filtered.length} {copy('papersFound')}
        </p>

        {/* Paper Grid */}
        {filtered.length === 0 ? (
          <ScrollReveal>
            <div className="bg-apple-gray-100 rounded-apple-lg p-16 text-center">
              <div className="text-5xl mb-4">🔬</div>
              <h3 className="text-xl font-semibold text-apple-gray-800 mb-2">
                {copy('noResults')}
              </h3>
              <p className="text-apple-gray-500">
                {copy('adjustYourSearchOrCategoryToFindPapers')}
              </p>
            </div>
          </ScrollReveal>
        ) : (
          <div className="grid gap-6 md:grid-cols-2">
            {filtered.map((paper: ResearchPaper, i: number) => {
              const colors = CATEGORY_COLORS[paper.category];
              const isExpanded = expandedId === paper.id;

              return (
                <ScrollReveal key={paper.id} delay={i * 80}>
                  <div className="card-hover bg-white rounded-apple-lg shadow-apple-card p-5 md:p-8 flex flex-col h-full">
                    {/* Category + Year */}
                    <div className="flex items-center justify-between mb-4">
                      <span
                        className={`inline-flex px-3 py-1 rounded-full text-[11px] font-semibold uppercase tracking-wider ${colors.bg} ${colors.text} border ${colors.border}`}
                      >
                        {paper.categoryLabel}
                      </span>
                      <span className="text-sm text-apple-gray-500 font-mono">{paper.year}</span>
                    </div>

                    {/* Title */}
                    <h3 className="text-base font-semibold text-apple-gray-800 leading-snug mb-3">
                      {paper.title}
                    </h3>

                    {/* Authors + Journal */}
                    <p className="text-sm text-apple-gray-500 mb-1 line-clamp-1">{paper.authors}</p>
                    <p className="text-sm text-gm-green-600 font-medium mb-4">{paper.journal}</p>

                    {/* GreenMind Relevanz */}
                    <div className="bg-gm-green-50/60 rounded-apple p-4 border border-gm-green-100/50 mb-4 flex-1">
                      <p className="text-[11px] font-semibold text-gm-green-700 uppercase tracking-wider mb-1.5">
                        {copy('relevanceToGreenMind')}
                      </p>
                      <p
                        className={`text-sm text-apple-gray-600 leading-relaxed ${!isExpanded ? 'line-clamp-3' : ''}`}
                      >
                        {paper.greenmindLink}
                      </p>
                    </div>

                    {/* Expandable Abstract */}
                    {isExpanded && (
                      <div className="bg-apple-gray-50 rounded-apple p-4 border border-apple-gray-200/50 mb-4">
                        <p className="text-[11px] font-semibold text-apple-gray-500 uppercase tracking-wider mb-1.5">
                          {copy('abstract')}
                        </p>
                        <p className="text-sm text-apple-gray-600 leading-relaxed">
                          {paper.abstract}
                        </p>
                      </div>
                    )}

                    {/* Actions */}
                    <div className="flex items-center gap-3 mt-auto pt-3">
                      <a
                        href={paper.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex-1 text-center px-4 py-2.5 bg-gm-green-500 text-white rounded-full text-sm font-medium hover:bg-gm-green-600 transition-colors duration-200 shadow-sm shadow-gm-green-500/20"
                      >
                        {copy('readPaper')}
                      </a>
                      <button
                        onClick={() => setExpandedId(isExpanded ? null : paper.id)}
                        className="px-4 py-2.5 bg-apple-gray-100 text-apple-gray-600 rounded-full text-sm font-medium hover:bg-apple-gray-200 transition-colors"
                      >
                        {isExpanded ? copy('less') : copy('details')}
                      </button>
                    </div>

                    {/* DOI */}
                    <p className="text-[11px] text-apple-gray-400 mt-3 font-mono truncate">
                      {copy('doi')} {paper.doi}
                    </p>
                  </div>
                </ScrollReveal>
              );
            })}
          </div>
        )}

        {/* Footer Note */}
        <ScrollReveal>
          <div className="text-center py-16 mt-8">
            <p className="text-sm text-apple-gray-500">
              {copy('allListedPapersArePeerReviewedOpenAccess')}
            </p>
            <p className="text-sm text-apple-gray-400 mt-1">{copy('lastUpdatedMay2026')}</p>
          </div>
        </ScrollReveal>
      </div>
    </div>
  );
}
