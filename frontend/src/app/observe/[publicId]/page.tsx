'use client';

import { useState } from 'react';
import { useParams } from 'next/navigation';
import {
    apiCreateSession,
    apiGetPlantContext,
    apiCreateEvaluation,
} from '@/lib/observe-api';
import { PublicPlantContext, PlantEvaluationCreate } from '@/types';

/* ── Option Definitions ─────────────────────────────────────────────── */

const COLOR_OPTIONS = [
    { key: 'saturated_green', label: 'Sattgrün', sub: 'optimal', icon: '🟢' },
    { key: 'light_green', label: 'Hellgrün', sub: '', icon: '🟡' },
    { key: 'yellowish', label: 'Gelblich', sub: '', icon: '🌕' },
    { key: 'spotted', label: 'Fleckig', sub: '', icon: '🔵' },
    { key: 'brown_dead', label: 'Braun / abgestorben', sub: '', icon: '🟤' },
] as const;

const STRUCTURE_OPTIONS = [
    { key: 'firm_taut', label: 'Fest / gespannt', icon: '💪' },
    { key: 'slightly_limp', label: 'Leicht schlaff', icon: '🍃' },
    { key: 'very_limp', label: 'Stark schlaff', icon: '😥' },
    { key: 'curled_deformed', label: 'Eingerollt / deformiert', icon: '🌀' },
] as const;

const GROWTH_OPTIONS = [
    { key: 'strong', label: 'Stark wachsend', icon: '🚀' },
    { key: 'normal', label: 'Normal', icon: '📈' },
    { key: 'slow', label: 'Langsam', icon: '🐢' },
    { key: 'none', label: 'Kein Wachstum', icon: '⏸️' },
] as const;

const WATER_OPTIONS = [
    { key: 'too_dry', label: 'Zu trocken', icon: '🏜️' },
    { key: 'optimal', label: 'Optimal', icon: '💧' },
    { key: 'too_wet', label: 'Zu nass', icon: '🌊' },
] as const;

const ANOMALY_OPTIONS = [
    { key: 'spots', label: 'Flecken', icon: '🔴' },
    { key: 'holes', label: 'Löcher', icon: '🕳️' },
    { key: 'mold', label: 'Schimmel', icon: '🦠' },
    { key: 'pests', label: 'Schädlinge sichtbar', icon: '🐛' },
    { key: 'none', label: 'Keine Auffälligkeiten', icon: '✅' },
] as const;

const OVERALL_LABELS = ['', 'Sehr schlecht', 'Schlecht', 'Mittel', 'Gut', 'Sehr gut'];
const OVERALL_COLORS = [
    '',
    'bg-red-500 text-white',
    'bg-orange-400 text-white',
    'bg-yellow-400 text-gray-900',
    'bg-emerald-400 text-white',
    'bg-emerald-600 text-white',
];

const TOTAL_STEPS = 6;

/* ── Reusable Components ────────────────────────────────────────────── */

function ProgressBar({ step }: { step: number }) {
    const pct = (step / TOTAL_STEPS) * 100;
    return (
        <div className="mb-6">
            <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
                    Schritt {step} von {TOTAL_STEPS}
                </span>
                <span className="text-xs font-bold text-emerald-600">{Math.round(pct)}%</span>
            </div>
            <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
                <div
                    className="h-full bg-gradient-to-r from-emerald-400 to-emerald-600 rounded-full transition-all duration-500 ease-out"
                    style={{ width: `${pct}%` }}
                />
            </div>
        </div>
    );
}

function StepCard({
    title,
    subtitle,
    children,
}: {
    title: string;
    subtitle?: string;
    children: React.ReactNode;
}) {
    return (
        <div className="animate-fade-in">
            <h2 className="text-xl font-bold text-gray-900 mb-1">{title}</h2>
            {subtitle && <p className="text-sm text-gray-500 mb-5">{subtitle}</p>}
            {!subtitle && <div className="mb-5" />}
            {children}
        </div>
    );
}

function OptionCard({
    label,
    icon,
    selected,
    onClick,
    sub,
}: {
    label: string;
    icon: string;
    selected: boolean;
    onClick: () => void;
    sub?: string;
}) {
    return (
        <button
            type="button"
            onClick={onClick}
            className={`w-full flex items-center gap-3 p-4 rounded-2xl border-2 transition-all duration-200 active:scale-[0.97] ${
                selected
                    ? 'border-emerald-500 bg-emerald-50 shadow-md shadow-emerald-500/10'
                    : 'border-gray-200 bg-white hover:border-gray-300 hover:shadow-sm'
            }`}
        >
            <span className="text-2xl flex-shrink-0">{icon}</span>
            <div className="text-left">
                <span className={`font-semibold text-sm ${selected ? 'text-emerald-700' : 'text-gray-800'}`}>
                    {label}
                </span>
                {sub && <span className="block text-xs text-gray-400">{sub}</span>}
            </div>
            {selected && (
                <span className="ml-auto text-emerald-500">
                    <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                        <path
                            fillRule="evenodd"
                            d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z"
                            clipRule="evenodd"
                        />
                    </svg>
                </span>
            )}
        </button>
    );
}

function ChipButton({
    label,
    icon,
    selected,
    onClick,
}: {
    label: string;
    icon: string;
    selected: boolean;
    onClick: () => void;
}) {
    return (
        <button
            type="button"
            onClick={onClick}
            className={`flex items-center gap-2 px-4 py-3 rounded-full border-2 transition-all duration-200 active:scale-[0.95] text-sm font-medium ${
                selected
                    ? 'border-emerald-500 bg-emerald-50 text-emerald-700 shadow-sm'
                    : 'border-gray-200 bg-white text-gray-700 hover:border-gray-300'
            }`}
        >
            <span>{icon}</span>
            <span>{label}</span>
        </button>
    );
}

function NavButtons({
    onBack,
    onNext,
    nextDisabled,
    nextLabel,
    loading,
}: {
    onBack?: () => void;
    onNext: () => void;
    nextDisabled: boolean;
    nextLabel?: string;
    loading?: boolean;
}) {
    return (
        <div className="flex gap-3 mt-8">
            {onBack && (
                <button
                    type="button"
                    onClick={onBack}
                    className="flex-1 py-4 rounded-2xl font-semibold text-gray-600 bg-gray-100 hover:bg-gray-200 active:scale-[0.97] transition-all"
                >
                    Zurück
                </button>
            )}
            <button
                type="button"
                onClick={onNext}
                disabled={nextDisabled || loading}
                className={`flex-[2] py-4 rounded-2xl font-bold text-lg transition-all duration-200 active:scale-[0.97] ${
                    nextDisabled || loading
                        ? 'bg-gray-200 text-gray-400 cursor-not-allowed'
                        : 'bg-gradient-to-r from-emerald-500 to-emerald-600 text-white shadow-lg shadow-emerald-500/25 hover:shadow-emerald-500/40'
                }`}
            >
                {loading ? (
                    <span className="flex items-center justify-center gap-2">
                        <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24">
                            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                        </svg>
                        Wird gesendet...
                    </span>
                ) : (
                    nextLabel || 'Weiter'
                )}
            </button>
        </div>
    );
}

/* ── Main Page Component ────────────────────────────────────────────── */

export default function PlantEvaluationPage() {
    const params = useParams() as { publicId: string };
    const publicId = params.publicId;

    // Flow state
    const [phase, setPhase] = useState<'START' | 'EVAL' | 'DONE'>('START');
    const [step, setStep] = useState(1);
    const [sessionToken, setSessionToken] = useState<string | null>(null);
    const [context, setContext] = useState<PublicPlantContext | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Evaluation data – no defaults (forces conscious selection)
    const [overallScore, setOverallScore] = useState<number | null>(null);
    const [colorRaw, setColorRaw] = useState<string | null>(null);
    const [structureRaw, setStructureRaw] = useState<string | null>(null);
    const [growthRaw, setGrowthRaw] = useState<string | null>(null);
    const [anomaliesRaw, setAnomaliesRaw] = useState<string[]>([]);
    const [waterRaw, setWaterRaw] = useState<string | null>(null);
    const [detailNotes, setDetailNotes] = useState('');

    /* ── Handlers ──────────────────────────────────────────────────── */

    const handleStart = async () => {
        setLoading(true);
        setError(null);
        try {
            const session = await apiCreateSession(publicId);
            setSessionToken(session.session_token);
            const ctx = await apiGetPlantContext(session.session_token);
            setContext(ctx);
            setPhase('EVAL');
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Zugang ungültig oder abgelaufen.');
        } finally {
            setLoading(false);
        }
    };

    const handleSubmit = async () => {
        if (!sessionToken || overallScore === null || !colorRaw || !structureRaw || !growthRaw || !waterRaw || anomaliesRaw.length === 0) return;
        setLoading(true);
        setError(null);
        try {
            const payload: PlantEvaluationCreate = {
                overall_score: overallScore,
                color_raw: colorRaw,
                structure_raw: structureRaw,
                growth_raw: growthRaw,
                water_raw: waterRaw,
                anomalies_raw: anomaliesRaw,
                detail_notes: detailNotes || undefined,
            };
            await apiCreateEvaluation(sessionToken, payload);
            setPhase('DONE');
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Fehler beim Speichern.');
        } finally {
            setLoading(false);
        }
    };

    const toggleAnomaly = (key: string) => {
        if (key === 'none') {
            setAnomaliesRaw(['none']);
            return;
        }
        setAnomaliesRaw((prev) => {
            const without = prev.filter((a) => a !== 'none');
            return without.includes(key) ? without.filter((a) => a !== key) : [...without, key];
        });
    };

    const goNext = () => {
        if (step < TOTAL_STEPS) setStep(step + 1);
        else handleSubmit();
    };

    const goBack = () => {
        if (step > 1) setStep(step - 1);
    };

    /* ── Step validity checks ──────────────────────────────────────── */

    const isStepValid = (): boolean => {
        switch (step) {
            case 1: return overallScore !== null;
            case 2: return colorRaw !== null;
            case 3: return structureRaw !== null;
            case 4: return growthRaw !== null;
            case 5: return anomaliesRaw.length > 0;
            case 6: return waterRaw !== null;
            default: return false;
        }
    };

    /* ── Render ─────────────────────────────────────────────────────── */

    return (
        <div className="min-h-screen flex flex-col">
            {/* Header */}
            <header className="bg-gradient-to-r from-emerald-600 to-emerald-700 text-white px-4 py-4 shadow-lg">
                <div className="max-w-md mx-auto flex items-center justify-between">
                    <div>
                        <h1 className="text-lg font-bold tracking-tight">GreenMind</h1>
                        {context && (
                            <p className="text-emerald-100 text-xs mt-0.5">{context.name}</p>
                        )}
                    </div>
                    <span className="text-2xl">🌱</span>
                </div>
            </header>

            <main className="flex-1 w-full max-w-md mx-auto px-4 py-6 flex flex-col">
                {/* Error banner */}
                {error && (
                    <div className="mb-4 p-4 bg-red-50 border-l-4 border-red-500 text-red-700 rounded-xl shadow-sm">
                        <p className="font-medium text-sm">{error}</p>
                    </div>
                )}

                {/* ── START ──────────────────────────────────────────── */}
                {phase === 'START' && (
                    <div className="flex-1 flex flex-col items-center justify-center text-center space-y-6 max-w-xs mx-auto">
                        <div className="w-24 h-24 bg-emerald-100 text-emerald-600 rounded-full flex items-center justify-center text-4xl shadow-inner">
                            🔍
                        </div>
                        <div>
                            <h2 className="text-2xl font-bold text-gray-800 mb-2">Pflanze bewerten</h2>
                            <p className="text-gray-500 text-sm">
                                6 kurze Fragen zum Zustand dieser Pflanze. Dauert ca. 30 Sekunden.
                            </p>
                        </div>
                        <button
                            onClick={handleStart}
                            disabled={loading}
                            className="w-full py-4 px-6 bg-gradient-to-r from-emerald-500 to-emerald-600 text-white rounded-2xl font-bold shadow-lg shadow-emerald-500/30 hover:shadow-emerald-500/40 active:scale-[0.97] transition-all text-lg"
                        >
                            {loading ? 'Lade Daten...' : 'Bewertung starten'}
                        </button>
                    </div>
                )}

                {/* ── EVALUATION WIZARD ──────────────────────────────── */}
                {phase === 'EVAL' && (
                    <div>
                        <ProgressBar step={step} />

                        {/* Step 1: Overall Score */}
                        {step === 1 && (
                            <StepCard
                                title="Gesamtzustand"
                                subtitle="Wie geht es der Pflanze insgesamt?"
                            >
                                <div className="grid grid-cols-5 gap-2">
                                    {[1, 2, 3, 4, 5].map((score) => (
                                        <button
                                            key={score}
                                            type="button"
                                            onClick={() => setOverallScore(score)}
                                            className={`flex flex-col items-center py-4 rounded-2xl border-2 transition-all duration-200 active:scale-[0.93] ${
                                                overallScore === score
                                                    ? `${OVERALL_COLORS[score]} border-transparent shadow-lg`
                                                    : 'border-gray-200 bg-white hover:border-gray-300 text-gray-700'
                                            }`}
                                        >
                                            <span className="text-2xl font-bold">{score}</span>
                                        </button>
                                    ))}
                                </div>
                                <div className="flex justify-between text-xs text-gray-400 mt-2 px-1">
                                    <span>Sehr schlecht</span>
                                    <span>Sehr gut</span>
                                </div>
                                {overallScore !== null && (
                                    <div className={`mt-4 text-center py-2 rounded-xl text-sm font-semibold ${OVERALL_COLORS[overallScore]} bg-opacity-10`}>
                                        {OVERALL_LABELS[overallScore]}
                                    </div>
                                )}
                                {/* Adaptive detail when score <= 2 */}
                                {overallScore !== null && overallScore <= 2 && (
                                    <div className="mt-4 animate-fade-in">
                                        <label className="block text-sm font-semibold text-gray-700 mb-2">
                                            Was fällt besonders auf? (optional)
                                        </label>
                                        <textarea
                                            rows={3}
                                            value={detailNotes}
                                            onChange={(e) => setDetailNotes(e.target.value)}
                                            placeholder="Beschreiben Sie auffällige Symptome..."
                                            className="w-full px-4 py-3 border-2 border-gray-200 rounded-2xl bg-white focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500 outline-none transition-all text-gray-800 resize-none text-sm"
                                        />
                                    </div>
                                )}
                                <NavButtons
                                    onNext={goNext}
                                    nextDisabled={!isStepValid()}
                                />
                            </StepCard>
                        )}

                        {/* Step 2: Leaf Color */}
                        {step === 2 && (
                            <StepCard
                                title="Blattfarbe"
                                subtitle="Welche Farbe haben die Blätter?"
                            >
                                <div className="space-y-2">
                                    {COLOR_OPTIONS.map((opt) => (
                                        <OptionCard
                                            key={opt.key}
                                            label={opt.label}
                                            icon={opt.icon}
                                            sub={opt.sub}
                                            selected={colorRaw === opt.key}
                                            onClick={() => setColorRaw(opt.key)}
                                        />
                                    ))}
                                </div>
                                <NavButtons
                                    onBack={goBack}
                                    onNext={goNext}
                                    nextDisabled={!isStepValid()}
                                />
                            </StepCard>
                        )}

                        {/* Step 3: Leaf Structure */}
                        {step === 3 && (
                            <StepCard
                                title="Blattstruktur / Spannung"
                                subtitle="Wie fühlen sich die Blätter an?"
                            >
                                <div className="space-y-2">
                                    {STRUCTURE_OPTIONS.map((opt) => (
                                        <OptionCard
                                            key={opt.key}
                                            label={opt.label}
                                            icon={opt.icon}
                                            selected={structureRaw === opt.key}
                                            onClick={() => setStructureRaw(opt.key)}
                                        />
                                    ))}
                                </div>
                                <NavButtons
                                    onBack={goBack}
                                    onNext={goNext}
                                    nextDisabled={!isStepValid()}
                                />
                            </StepCard>
                        )}

                        {/* Step 4: Growth */}
                        {step === 4 && (
                            <StepCard
                                title="Wachstum"
                                subtitle="Wie entwickelt sich die Pflanze?"
                            >
                                <div className="space-y-2">
                                    {GROWTH_OPTIONS.map((opt) => (
                                        <OptionCard
                                            key={opt.key}
                                            label={opt.label}
                                            icon={opt.icon}
                                            selected={growthRaw === opt.key}
                                            onClick={() => setGrowthRaw(opt.key)}
                                        />
                                    ))}
                                </div>
                                <NavButtons
                                    onBack={goBack}
                                    onNext={goNext}
                                    nextDisabled={!isStepValid()}
                                />
                            </StepCard>
                        )}

                        {/* Step 5: Anomalies (Multi-Select) */}
                        {step === 5 && (
                            <StepCard
                                title="Auffälligkeiten"
                                subtitle="Gibt es sichtbare Probleme? (Mehrfachauswahl)"
                            >
                                <div className="flex flex-wrap gap-2">
                                    {ANOMALY_OPTIONS.map((opt) => (
                                        <ChipButton
                                            key={opt.key}
                                            label={opt.label}
                                            icon={opt.icon}
                                            selected={anomaliesRaw.includes(opt.key)}
                                            onClick={() => toggleAnomaly(opt.key)}
                                        />
                                    ))}
                                </div>
                                <NavButtons
                                    onBack={goBack}
                                    onNext={goNext}
                                    nextDisabled={!isStepValid()}
                                />
                            </StepCard>
                        )}

                        {/* Step 6: Water State */}
                        {step === 6 && (
                            <StepCard
                                title="Wasserzustand"
                                subtitle="Wie schätzen Sie die Feuchtigkeit ein?"
                            >
                                <div className="space-y-2">
                                    {WATER_OPTIONS.map((opt) => (
                                        <OptionCard
                                            key={opt.key}
                                            label={opt.label}
                                            icon={opt.icon}
                                            selected={waterRaw === opt.key}
                                            onClick={() => setWaterRaw(opt.key)}
                                        />
                                    ))}
                                </div>
                                <NavButtons
                                    onBack={goBack}
                                    onNext={goNext}
                                    nextDisabled={!isStepValid()}
                                    nextLabel="Bewertung absenden"
                                    loading={loading}
                                />
                            </StepCard>
                        )}
                    </div>
                )}

                {/* ── DONE ──────────────────────────────────────────── */}
                {phase === 'DONE' && (
                    <div className="flex-1 flex flex-col items-center justify-center text-center space-y-4">
                        <div className="w-24 h-24 bg-emerald-100 text-emerald-600 rounded-full flex items-center justify-center text-5xl shadow-inner mb-2">
                            ✅
                        </div>
                        <h2 className="text-2xl font-bold text-gray-800">
                            Danke – Bewertung gespeichert
                        </h2>
                        <p className="text-gray-500 text-sm max-w-xs mx-auto">
                            Ihre Pflanzenbewertung wurde erfolgreich übermittelt und wird für die GreenMind Forschung verwendet. Sie können dieses Fenster nun schliessen.
                        </p>
                    </div>
                )}
            </main>
        </div>
    );
}
