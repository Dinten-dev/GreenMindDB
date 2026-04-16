'use client';

import { useState, useEffect } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { 
    apiGetPlant, 
    apiAssignSensor, 
    apiGetSensorHistory, 
    apiCreateObservationAccess, 
    apiRevokeObservationAccess 
} from '@/lib/plants-api';
import { apiListSensors } from '@/lib/api';
import { Plant, PlantSensorAssignment, ObservationAccess, SensorInfo } from '@/types';

export default function PlantDetailPage() {
    const params = useParams() as { plantId: string };
    const plantId = params.plantId;

    const [plant, setPlant] = useState<Plant | null>(null);
    const [history, setHistory] = useState<PlantSensorAssignment[]>([]);
    const [availableSensors, setAvailableSensors] = useState<SensorInfo[]>([]);
    const [access, setAccess] = useState<ObservationAccess | null>(null);
    const [loading, setLoading] = useState(true);
    
    // Assign form
    const [showAssign, setShowAssign] = useState(false);
    const [selectedSensor, setSelectedSensor] = useState('');
    const [assignNote, setAssignNote] = useState('');
    const [assigning, setAssigning] = useState(false);

    useEffect(() => {
        loadData();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [plantId]);

    const loadData = async () => {
        try {
            const [p, h, s] = await Promise.all([
                apiGetPlant(plantId),
                apiGetSensorHistory(plantId),
                apiListSensors() // in real app, might filter unassigned
            ]);
            setPlant(p);
            setHistory(h);
            setAvailableSensors(s);
            
            // Try to load access
            try {
                const a = await apiCreateObservationAccess(plantId);
                setAccess(a);
            } catch {
                // Ignore if creating fails or already revoked 
                // Currently get_or_create logic in backend handles it.
            }
            
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const handleAssign = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!selectedSensor) return;
        setAssigning(true);
        try {
            await apiAssignSensor(plantId, selectedSensor, assignNote);
            setShowAssign(false);
            setAssignNote('');
            await loadData();
        } catch (err) {
            console.error('Assign failed', err);
        } finally {
            setAssigning(false);
        }
    };

    const handleRevokeQR = async () => {
        if (!confirm('Zugang widerrufen? Alter QR-Code wird ungültig.')) return;
        try {
            const newAccess = await apiRevokeObservationAccess(plantId);
            setAccess(newAccess);
        } catch (err) {
            console.error(err);
        }
    };

    const handleGenerateQR = async () => {
        try {
            const a = await apiCreateObservationAccess(plantId);
            setAccess(a);
        } catch (err) {
            console.error(err);
        }
    };

    if (loading) return <div className="p-8 text-gray-500">Lade Struktur...</div>;
    if (!plant) return <div className="p-8 text-red-500">Pflanze nicht gefunden.</div>;

    const currentAssignment = history.find(h => h.is_active);

    return (
        <div className="space-y-6">
            <div className="flex items-center gap-4">
                <Link href="/app/plants" className="p-2 bg-white/40 rounded-xl hover:bg-white/60">
                    ←
                </Link>
                <div>
                    <h1 className="text-2xl font-bold text-gray-800">{plant.name}</h1>
                    <p className="text-sm text-gray-400">ID: {plant.plant_code || plant.id}</p>
                </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div className="glass-card p-6">
                    <h2 className="text-lg font-semibold mb-4">Sensor-Zuordnung</h2>
                    {currentAssignment ? (
                        <div className="bg-emerald-50 border border-emerald-100 rounded-xl p-4">
                            <div className="text-emerald-800 font-medium">Aktiver Sensor</div>
                            <div className="text-sm text-emerald-600 mt-1 font-mono">
                                ID: {currentAssignment.sensor_id}
                            </div>
                            <button
                                onClick={() => setShowAssign(true)}
                                className="mt-3 text-sm text-emerald-700 bg-white/50 px-3 py-1.5 rounded-lg hover:bg-white transition"
                            >
                                Sensor wechseln
                            </button>
                        </div>
                    ) : (
                        <div className="bg-black/[0.02] rounded-xl p-6 text-center border border-black/[0.04]">
                            <p className="text-gray-500 text-sm mb-3">Kein Sensor zugeordnet.</p>
                            <button
                                onClick={() => setShowAssign(true)}
                                className="px-4 py-2 bg-gray-800 text-white rounded-full text-sm font-medium hover:bg-black transition"
                            >
                                Sensor zuordnen
                            </button>
                        </div>
                    )}

                    {history.length > 0 && (
                        <div className="mt-6">
                            <h3 className="text-sm font-medium text-gray-400 mb-3 uppercase tracking-wider">Historie</h3>
                            <div className="space-y-2">
                                {history.map(h => (
                                    <div key={h.id} className="text-xs bg-white/40 p-2 rounded-lg flex justify-between">
                                        <span className="font-mono text-gray-600">{h.sensor_id.slice(0,8)}...</span>
                                        <span className="text-gray-400 text-right">
                                            {new Date(h.assigned_at).toLocaleDateString()} - 
                                            {h.unassigned_at ? new Date(h.unassigned_at).toLocaleDateString() : 'heute'}
                                        </span>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </div>

                <div className="glass-card p-6">
                    <h2 className="text-lg font-semibold mb-4">QR / Observation Access</h2>
                    {access && access.is_active ? (
                        <div className="space-y-4">
                            <div className="bg-white/60 p-4 rounded-xl border border-black/[0.04] text-sm">
                                <p className="text-gray-500 mb-1">Public ID</p>
                                <p className="font-mono text-gray-800 break-all">{access.public_id}</p>
                                <p className="text-xs text-gray-400 mt-2">Nutzungen: {access.usage_count}</p>
                            </div>
                            
                            <div className="flex gap-2">
                                <Link
                                    href={`/app/plants/${plant.id}/print-qr`}
                                    className="flex-1 text-center py-2 bg-gradient-to-r from-emerald-500 to-emerald-600 text-white rounded-xl text-sm font-medium hover:from-emerald-600 hover:to-emerald-700 shadow-sm"
                                >
                                    QR Code betrachten
                                </Link>
                                <button
                                    onClick={handleRevokeQR}
                                    className="px-4 py-2 bg-red-50 text-red-600 border border-red-200 rounded-xl text-sm hover:bg-red-100 transition"
                                >
                                    Widerrufen
                                </button>
                            </div>
                            <p className="text-xs text-gray-400 mt-2">
                                Die Public ID dient als sicherer Anker für das loginfreie Feldformular.
                            </p>
                        </div>
                    ) : (
                        <div className="bg-black/[0.02] rounded-xl p-6 text-center border border-black/[0.04]">
                            <p className="text-gray-500 text-sm mb-3">Kein aktiver Zugang generiert.</p>
                            <button
                                onClick={handleGenerateQR}
                                className="px-4 py-2 bg-gray-800 text-white rounded-full text-sm font-medium hover:bg-black transition"
                            >
                                Zugang generieren
                            </button>
                        </div>
                    )}
                </div>
            </div>

            {/* Modal for Assign */}
            {showAssign && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 backdrop-blur-sm">
                    <div className="glass-card p-6 w-full max-w-sm mx-4">
                        <h2 className="text-lg font-semibold mb-4">Sensor zuordnen</h2>
                        <form onSubmit={handleAssign} className="space-y-4">
                            <div>
                                <label className="block text-sm text-gray-600 mb-1.5">Sensor Auswählen</label>
                                <select
                                    required
                                    value={selectedSensor}
                                    onChange={e => setSelectedSensor(e.target.value)}
                                    className="w-full px-3 py-2 rounded-lg bg-white/60 text-sm"
                                >
                                    <option value="" disabled>Bitte wählen...</option>
                                    {availableSensors.map(s => (
                                        <option key={s.id} value={s.id}>{s.name || s.mac_address}</option>
                                    ))}
                                </select>
                            </div>
                            <div>
                                <label className="block text-sm text-gray-600 mb-1.5">Notiz</label>
                                <input
                                    type="text"
                                    value={assignNote}
                                    onChange={e => setAssignNote(e.target.value)}
                                    className="w-full px-3 py-2 rounded-lg bg-white/60 text-sm"
                                    placeholder="Grund für Wechsel, etc."
                                />
                            </div>
                            <div className="flex gap-2">
                                <button type="button" onClick={() => setShowAssign(false)} className="flex-1 py-2 bg-gray-100 text-gray-600 rounded-lg text-sm">Abbrechen</button>
                                <button type="submit" disabled={assigning} className="flex-1 py-2 bg-emerald-600 text-white rounded-lg text-sm disabled:opacity-50">Speichern</button>
                            </div>
                        </form>
                    </div>
                </div>
            )}
        </div>
    );
}
