'use client';

import { useState, useEffect, useCallback } from 'react';
import Link from 'next/link';
import { useLocale } from 'next-intl';
import { apiListPlants, apiCreatePlant } from '@/lib/plants-api';
import { apiListZones } from '@/lib/api';
import { Plant, Zone } from '@/types';

export default function PlantsPage() {
  const locale = useLocale();
  const [loadError, setLoadError] = useState(false);
  const [createError, setCreateError] = useState(false);
  const [plants, setPlants] = useState<Plant[]>([]);
  const [zones, setZones] = useState<Zone[]>([]);
  const [loading, setLoading] = useState(true);

  // Create Form
  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState('');
  const [plantCode, setPlantCode] = useState('');
  const [zoneId, setZoneId] = useState('');

  const loadData = useCallback(async () => {
    setLoading(true);
    setLoadError(false);
    try {
      const [pData, zData] = await Promise.all([apiListPlants(), apiListZones()]);
      setPlants(pData);
      setZones(zData);
      if (zData.length > 0 && !zoneId) {
        setZoneId(zData[0].id);
      }
    } catch (err) {
      setLoadError(true);
      console.error(err);
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !zoneId) return;
    setCreating(true);
    setCreateError(false);
    try {
      await apiCreatePlant({
        name,
        plant_code: plantCode,
        zone_id: zoneId,
      });
      setName('');
      setPlantCode('');
      setShowCreate(false);
      await loadData();
    } catch (err) {
      setCreateError(true);
      console.error(err);
    } finally {
      setCreating(false);
    }
  };

  if (loading) {
    return (
      <div className="animate-pulse space-y-4">
        <div className="h-8 w-40 bg-black/[0.04] rounded-xl" />
        <div className="h-32 bg-black/[0.04] rounded-2xl" />
      </div>
    );
  }

  if (loadError)
    return (
      <div className="glass-card p-8">
        <h1 className="text-2xl font-semibold text-gray-800">Pflanzen</h1>
        <p role="alert" className="text-sm text-gray-600 mt-3">
          Deine Pflanzen konnten nicht geladen werden.
        </p>
        <button
          onClick={loadData}
          className="mt-4 px-5 py-3 rounded-xl bg-emerald-600 text-white text-sm"
        >
          Erneut laden
        </button>
      </div>
    );

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-800 tracking-tight">Pflanzen</h1>
          <p className="text-sm text-gray-400 mt-1">
            Deine Pflanzen, ihre Standorte und zugeordneten Sensoren.
          </p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="px-4 py-2 bg-emerald-600 text-white rounded-full text-sm font-medium hover:bg-emerald-700 transition-all duration-200 shadow-sm whitespace-nowrap"
        >
          + Neue Pflanze
        </button>
      </div>

      {/* Create Modal */}
      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 backdrop-blur-sm">
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="create-plant-title"
            className="glass-card p-6 sm:p-8 w-full max-w-md mx-4 max-h-[90dvh] overflow-y-auto shadow-xl"
          >
            <button
              onClick={() => setShowCreate(false)}
              className="block ml-auto mb-3 text-sm text-gray-600 px-3 py-2 rounded-lg hover:bg-gray-50"
            >
              Schliessen ×
            </button>
            <h2 id="create-plant-title" className="text-xl font-semibold text-gray-800 mb-6">
              Pflanze erfassen
            </h2>
            {createError && (
              <p role="alert" className="mb-4 text-sm text-red-700">
                Die Pflanze konnte nicht gespeichert werden. Bitte erneut versuchen.
              </p>
            )}
            {zones.length === 0 ? (
              <div className="text-sm text-gray-600 mb-4">
                Ordne deine Pflanzen einem Standort zu.
                <Link
                  href={`/${locale}/app/zones`}
                  className="block mt-3 text-emerald-700 underline"
                >
                  Erste Zone erstellen →
                </Link>
              </div>
            ) : (
              <form onSubmit={handleCreate} className="space-y-4">
                <div>
                  <label
                    htmlFor="plant-zone"
                    className="block text-sm font-medium text-gray-600 mb-1.5"
                  >
                    Zone
                  </label>
                  <select
                    id="plant-zone"
                    value={zoneId}
                    onChange={(e) => setZoneId(e.target.value)}
                    className="w-full px-4 py-2.5 rounded-xl bg-white/60 border border-black/[0.06] text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-emerald-500/30"
                  >
                    {zones.map((z) => (
                      <option key={z.id} value={z.id}>
                        {z.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label
                    htmlFor="plant-name"
                    className="block text-sm font-medium text-gray-600 mb-1.5"
                  >
                    Name
                  </label>
                  <input
                    type="text"
                    id="plant-name"
                    required
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    className="w-full px-4 py-2.5 rounded-xl bg-white/60 border border-black/[0.06] text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-emerald-500/30"
                    placeholder="z.B. Tomate 01"
                  />
                </div>
                <div>
                  <label
                    htmlFor="plant-code"
                    className="block text-sm font-medium text-gray-600 mb-1.5"
                  >
                    Interne ID (Optional)
                  </label>
                  <input
                    type="text"
                    id="plant-code"
                    value={plantCode}
                    onChange={(e) => setPlantCode(e.target.value)}
                    className="w-full px-4 py-2.5 rounded-xl bg-white/60 border border-black/[0.06] text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-emerald-500/30"
                    placeholder="z.B. TOM-2026-A1"
                  />
                </div>
                <div className="flex gap-3 pt-2">
                  <button
                    type="button"
                    onClick={() => setShowCreate(false)}
                    className="flex-1 py-2.5 bg-black/[0.04] text-gray-600 rounded-xl text-sm font-medium hover:bg-black/[0.06] transition-colors"
                  >
                    Abbrechen
                  </button>
                  <button
                    type="submit"
                    disabled={creating}
                    className="flex-1 py-2.5 bg-emerald-600 text-white rounded-xl text-sm font-medium hover:bg-emerald-700 transition-all duration-200 disabled:opacity-50 shadow-sm"
                  >
                    {creating ? 'Speichere…' : 'Anlegen'}
                  </button>
                </div>
              </form>
            )}
          </div>
        </div>
      )}

      {/* List */}
      {plants.length === 0 ? (
        <div className="glass-card p-12 text-center">
          <div className="text-4xl mb-4 text-emerald-500">⍋</div>
          <h3 className="text-lg font-semibold text-gray-800 mb-2">Noch keine Pflanzen</h3>
          <p className="text-sm text-gray-400 mb-4">
            Erfasse deine erste Pflanze und ordne ihr anschliessend einen Sensor zu.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {plants.map((p) => {
            const zoneName = zones.find((z) => z.id === p.zone_id)?.name || 'Unbekannt';
            return (
              <Link
                key={p.id}
                href={`/${locale}/app/plants/${p.id}`}
                className="block glass-card p-5 hover:border-emerald-300 transition-colors"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h3 className="break-words font-semibold text-gray-800">{p.name}</h3>
                    <p className="text-xs text-gray-400 mt-0.5">{p.plant_code || 'Ohne Code'}</p>
                  </div>
                  {p.current_sensor_id && (
                    <span className="inline-flex px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-50 text-emerald-600">
                      Sensor zugeordnet
                    </span>
                  )}
                </div>
                <div className="mt-4 pt-4 border-t border-black/[0.04] flex items-center justify-between text-sm">
                  <span className="text-gray-500">Zone: {zoneName}</span>
                  <span className="text-gray-400">→</span>
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
