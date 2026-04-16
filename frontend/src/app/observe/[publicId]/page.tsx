'use client';

import { useState, useEffect, useRef } from 'react';
import { useParams } from 'next/navigation';
import { 
    apiCreateSession, 
    apiGetPlantContext, 
    apiCreateObservation, 
    apiUploadObservationPhoto 
} from '@/lib/observe-api';
import { PublicPlantContext } from '@/types';

type Step = 'START' | 'FORM' | 'PHOTO' | 'DONE';

export default function PublicObservationApp() {
    const params = useParams() as { publicId: string };
    const publicId = params.publicId;

    const [step, setStep] = useState<Step>('START');
    const [sessionToken, setSessionToken] = useState<string | null>(null);
    const [context, setContext] = useState<PublicPlantContext | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Form State
    const [wellbeingScore, setWellbeingScore] = useState<number>(3);
    const [plantCondition, setPlantCondition] = useState<string>('good');
    const [leafDroop, setLeafDroop] = useState<boolean>(false);
    const [spotsPresent, setSpotsPresent] = useState<boolean>(false);
    const [notes, setNotes] = useState<string>('');
    const [observationId, setObservationId] = useState<string | null>(null);

    // Photo State
    const fileInputRef = useRef<HTMLInputElement>(null);
    const [photoUploading, setPhotoUploading] = useState(false);

    const handleStart = async () => {
        setLoading(true);
        setError(null);
        try {
            const session = await apiCreateSession(publicId);
            setSessionToken(session.session_token);
            
            const ctx = await apiGetPlantContext(session.session_token);
            setContext(ctx);
            setStep('FORM');
        } catch (err) {
            console.error(err);
            const errorMessage = err instanceof Error ? err.message : 'Zugang ungültig oder abgelaufen.';
            setError(errorMessage);
        } finally {
            setLoading(false);
        }
    };

    const handleFormSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!sessionToken) return;
        setLoading(true);
        setError(null);
        try {
            const obs = await apiCreateObservation(sessionToken, {
                wellbeing_score: wellbeingScore,
                plant_condition: plantCondition,
                leaf_droop: leafDroop,
                spots_present: spotsPresent,
                notes: notes || undefined,
            });
            setObservationId(obs.id);
            setStep('PHOTO');
        } catch (err) {
            console.error(err);
            const errorMessage = err instanceof Error ? err.message : 'Fehler beim Speichern der Beobachtung.';
            setError(errorMessage);
        } finally {
            setLoading(false);
        }
    };

    const handlePhotoUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file || !sessionToken || !observationId) return;

        setPhotoUploading(true);
        setError(null);
        try {
            await apiUploadObservationPhoto(sessionToken, observationId, file);
            setStep('DONE');
        } catch (err) {
            console.error(err);
            const errorMessage = err instanceof Error ? err.message : 'Upload fehlgeschlagen.';
            setError(errorMessage);
        } finally {
            setPhotoUploading(false);
        }
    };

    return (
        <div className="min-h-screen bg-gray-50 flex flex-col font-sans">
            <header className="bg-emerald-600 text-white p-4 shadow-md safe-top">
                <div className="max-w-md mx-auto flex items-center justify-between">
                    <h1 className="text-xl font-bold tracking-tight">GreenMind Field App</h1>
                    <span className="text-2xl">🌱</span>
                </div>
            </header>

            <main className="flex-1 w-full max-w-md mx-auto p-4 flex flex-col pb-safe">
                {error && (
                    <div className="mb-4 p-4 bg-red-50 border-l-4 border-red-500 text-red-700 rounded-md shadow-sm">
                        <p className="font-medium text-sm">{error}</p>
                    </div>
                )}

                {step === 'START' && (
                    <div className="flex-1 flex flex-col items-center justify-center text-center space-y-6 max-w-xs mx-auto">
                        <div className="w-24 h-24 bg-emerald-100 text-emerald-600 rounded-full flex items-center justify-center text-4xl shadow-inner">
                            🔍
                        </div>
                        <div>
                            <h2 className="text-2xl font-bold text-gray-800 mb-2">Pflanze bewerten</h2>
                            <p className="text-gray-500 text-sm">
                                Starten Sie eine sichere, kurzlebige Sitzung, um den Zustand dieser Pflanze zu übermitteln.
                            </p>
                        </div>
                        <button
                            onClick={handleStart}
                            disabled={loading}
                            className="w-full py-4 px-6 bg-gradient-to-r from-emerald-500 to-emerald-600 text-white rounded-2xl font-bold shadow-lg shadow-emerald-500/30 hover:shadow-emerald-500/40 active:scale-95 transition-all text-lg"
                        >
                            {loading ? 'Lade Daten...' : 'Beobachtung starten'}
                        </button>
                    </div>
                )}

                {step === 'FORM' && context && (
                    <div className="animate-in fade-in slide-in-from-bottom-4 duration-300">
                        <div className="bg-white rounded-2xl shadow-sm border border-gray-100 overflow-hidden mb-6">
                            <div className="p-4 border-b border-gray-50 bg-gray-50">
                                <p className="text-xs text-gray-500 uppercase tracking-wide font-semibold mb-1">Ausgewählte Pflanze</p>
                                <h2 className="text-xl font-bold text-gray-900">{context.name}</h2>
                                {context.plant_code && <p className="text-sm text-gray-400 mt-0.5">{context.plant_code}</p>}
                            </div>
                            
                            <form onSubmit={handleFormSubmit} className="p-4 space-y-6">
                                {/* Wellbeing Score Slider */}
                                <div>
                                    <label className="block text-sm font-semibold text-gray-800 mb-2">Allgemeiner Eindruck (1-5)</label>
                                    <input 
                                        type="range" 
                                        min="1" max="5" 
                                        value={wellbeingScore}
                                        onChange={(e) => setWellbeingScore(Number(e.target.value))}
                                        className="w-full accent-emerald-600"
                                    />
                                    <div className="flex justify-between text-xs text-gray-400 mt-2 font-medium">
                                        <span>Schlecht (1)</span>
                                        <span className="text-emerald-600 text-lg font-bold">{wellbeingScore}</span>
                                        <span>Perfekt (5)</span>
                                    </div>
                                </div>

                                {/* Condition Select */}
                                <div>
                                    <label className="block text-sm font-semibold text-gray-800 mb-2">Wachstumsphase / Zustand</label>
                                    <select 
                                        value={plantCondition} 
                                        onChange={e => setPlantCondition(e.target.value)}
                                        className="w-full px-4 py-3 border border-gray-200 rounded-xl bg-gray-50 focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500 outline-none transition-all text-gray-800"
                                    >
                                        <option value="good">Gut wachsend</option>
                                        <option value="medium">Stagniert</option>
                                        <option value="poor">Verwelkt / Gestresst</option>
                                    </select>
                                </div>
                                
                                {/* Checks */}
                                <div className="space-y-3 p-4 bg-gray-50 rounded-xl border border-gray-100">
                                    <label className="flex items-center gap-3">
                                        <input 
                                            type="checkbox" 
                                            checked={leafDroop}
                                            onChange={e => setLeafDroop(e.target.checked)}
                                            className="w-5 h-5 text-emerald-600 rounded focus:ring-emerald-500/20" 
                                        />
                                        <span className="text-gray-700 font-medium text-sm">Hängende Blätter</span>
                                    </label>
                                    <label className="flex items-center gap-3">
                                        <input 
                                            type="checkbox" 
                                            checked={spotsPresent}
                                            onChange={e => setSpotsPresent(e.target.checked)}
                                            className="w-5 h-5 text-emerald-600 rounded focus:ring-emerald-500/20" 
                                        />
                                        <span className="text-gray-700 font-medium text-sm">Verfärbungen / Flecken</span>
                                    </label>
                                </div>

                                {/* Notes */}
                                <div>
                                    <label className="block text-sm font-semibold text-gray-800 mb-2">Notizen (Optional)</label>
                                    <textarea 
                                        rows={3}
                                        value={notes}
                                        onChange={e => setNotes(e.target.value)}
                                        placeholder="Besonderheiten, Schädlinge..."
                                        className="w-full px-4 py-3 border border-gray-200 rounded-xl bg-gray-50 focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500 outline-none transition-all text-gray-800 resize-none"
                                    />
                                </div>

                                <button
                                    type="submit"
                                    disabled={loading}
                                    className="w-full py-4 bg-gray-900 text-white rounded-xl font-bold hover:bg-black active:scale-95 transition-all shadow-md mt-4 text-lg"
                                >
                                    {loading ? 'Speichere...' : 'Weiter zum Foto'}
                                </button>
                            </form>
                        </div>
                    </div>
                )}

                {step === 'PHOTO' && (
                    <div className="flex-1 flex flex-col items-center justify-center text-center space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-300">
                        <div className="bg-white p-8 rounded-3xl shadow-sm border border-gray-100 w-full">
                            <div className="w-16 h-16 bg-blue-50 text-blue-500 rounded-full flex items-center justify-center text-2xl mb-6 mx-auto">
                                📸
                            </div>
                            <h2 className="text-xl font-bold text-gray-800 mb-2">Ein Foto aufnehmen</h2>
                            <p className="text-gray-500 text-sm mb-8">
                                Ein Bild hilft uns, die biologischen Daten besser auszuwerten.
                            </p>
                            
                            <input 
                                type="file" 
                                accept="image/*" 
                                capture="environment" 
                                className="hidden" 
                                ref={fileInputRef}
                                onChange={handlePhotoUpload}
                            />
                            
                            <button
                                onClick={() => fileInputRef.current?.click()}
                                disabled={photoUploading}
                                className="w-full py-4 bg-emerald-600 text-white rounded-xl flex items-center justify-center gap-2 font-bold hover:bg-emerald-700 active:scale-95 transition-all shadow-md shadow-emerald-600/20 text-lg mb-4"
                            >
                                {photoUploading ? 'Wird hochgeladen...' : 'Kamera öffnen'}
                            </button>
                            
                            <button
                                onClick={() => setStep('DONE')}
                                disabled={photoUploading}
                                className="w-full py-4 bg-gray-100 text-gray-600 font-semibold rounded-xl hover:bg-gray-200 active:scale-95 transition-all"
                            >
                                Überspringen
                            </button>
                        </div>
                    </div>
                )}

                {step === 'DONE' && (
                    <div className="flex-1 flex flex-col items-center justify-center text-center space-y-4 animate-in zoom-in-95 duration-500">
                        <div className="w-24 h-24 bg-emerald-100 text-emerald-600 rounded-full flex items-center justify-center text-5xl shadow-inner mb-2 animate-bounce">
                            ✅
                        </div>
                        <h2 className="text-2xl font-bold text-gray-800">Vielen Dank!</h2>
                        <p className="text-gray-500 text-sm max-w-xs mx-auto">
                            Ihre biologische Bewertung wurde erfolgreich übermittelt. Sie können dieses Fenster nun schließen.
                        </p>
                    </div>
                )}
            </main>
        </div>
    );
}
