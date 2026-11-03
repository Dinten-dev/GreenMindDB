'use client';

import { useAuth } from '@/contexts/AuthContext';
import { useAppText } from '@/i18n/app-text';
import { useGatewayRemoval } from '@/hooks/useGatewayRemoval';
import RemoveGatewayDialog, { GatewayRemovalNotice } from '@/components/RemoveGatewayDialog';

import { useState, useEffect, useCallback, useRef } from 'react';
import {
  apiListGateways,
  apiListZones,
  apiGeneratePairingCode,
  gatewayHasWavIssue,
  GatewayInfo,
  Zone,
  PairingCode,
} from '@/lib/api';

export default function GatewaysPage() {
  const tx = useAppText();
  const removedIds = useRef(new Set<string>());
  const { user } = useAuth();
  const canManage = user?.role === 'owner' || user?.role === 'admin';
  const [loadError, setLoadError] = useState(false);

  const [gateways, setGateways] = useState<GatewayInfo[]>([]);
  const [zones, setZones] = useState<Zone[]>([]);
  const [loading, setLoading] = useState(true);

  // Pairing
  const [showPairing, setShowPairing] = useState(false);
  const [selectedZone, setSelectedZone] = useState('');
  const [pairingCode, setPairingCode] = useState<PairingCode | null>(null);
  const [generating, setGenerating] = useState(false);

  const refreshData = useCallback(async () => {
    const [gws, zs] = await Promise.all([apiListGateways(), apiListZones()]);
    setGateways(gws.filter((gateway) => !removedIds.current.has(gateway.id)));
    setZones(zs);
    setLoadError(false);
  }, []);
  const loadData = useCallback(async () => {
    try {
      await refreshData();
    } catch {
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }, [refreshData]);
  useEffect(() => {
    void loadData();
  }, [loadData]);
  const removal = useGatewayRemoval({
    onRemoved: (id) => {
      removedIds.current.add(id);
      setGateways((previous) => previous.filter((gateway) => gateway.id !== id));
    },
    refresh: refreshData,
  });

  const handleGenerateCode = async () => {
    if (!selectedZone) return;
    setGenerating(true);
    try {
      const code = await apiGeneratePairingCode(selectedZone);
      setPairingCode(code);
    } catch (err) {
      console.error(err);
    } finally {
      setGenerating(false);
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

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-800 tracking-tight">{tx('Gateways')}</h1>
          <p className="text-sm text-gray-400 mt-1">{tx('Raspberry Pi Gateways verwalten')}</p>
        </div>
        <button
          onClick={() => {
            setShowPairing(true);
            setPairingCode(null);
          }}
          disabled={zones.length === 0 || !canManage}
          className="px-4 py-2 bg-gradient-to-r from-emerald-500 to-emerald-600 text-white rounded-full text-sm font-medium hover:from-emerald-600 hover:to-emerald-700 transition-all duration-200 shadow-sm disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
        >
          {tx('+ Gateway pairen')}
        </button>
      </div>

      <GatewayRemovalNotice notice={removal.notice} busy={removal.busy} onRetry={removal.retry} />
      {loadError && (
        <p role="alert">
          {tx(
            'Deine Daten konnten nicht geladen werden. Bitte prüfe die Verbindung und versuche es erneut.'
          )}{' '}
          <button onClick={() => void loadData()}>{tx('Erneut laden')}</button>
        </p>
      )}
      {/* Pairing Modal */}
      {showPairing && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 backdrop-blur-sm">
          <div className="glass-card p-8 w-full max-w-md mx-4 shadow-xl">
            <h2 className="text-xl font-semibold text-gray-800 mb-6">{tx('Gateway pairen')}</h2>

            {!pairingCode ? (
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-gray-600 mb-1.5">
                    {tx('Zone')}
                  </label>
                  <select
                    value={selectedZone}
                    onChange={(e) => setSelectedZone(e.target.value)}
                    className="w-full px-4 py-2.5 rounded-xl bg-white/60 border border-black/[0.06] text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-emerald-500/30 backdrop-blur-sm"
                  >
                    <option value="">{tx('Zone wählen…')}</option>
                    {zones.map((z) => (
                      <option key={z.id} value={z.id}>
                        {z.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="flex gap-3 pt-2">
                  <button
                    type="button"
                    onClick={() => setShowPairing(false)}
                    className="flex-1 py-2.5 bg-black/[0.04] text-gray-600 rounded-xl text-sm font-medium hover:bg-black/[0.06] transition-colors"
                  >
                    {tx('Abbrechen')}
                  </button>
                  <button
                    onClick={handleGenerateCode}
                    disabled={!selectedZone || generating}
                    className="flex-1 py-2.5 bg-gradient-to-r from-emerald-500 to-emerald-600 text-white rounded-xl text-sm font-medium hover:from-emerald-600 hover:to-emerald-700 transition-all duration-200 disabled:opacity-50 shadow-sm"
                  >
                    {generating ? tx('Generiere…') : tx('Code erzeugen')}
                  </button>
                </div>
              </div>
            ) : (
              <div className="text-center space-y-4">
                <p className="text-sm text-gray-400">
                  {tx('Gib diesen Code auf dem Raspberry Pi ein:')}
                </p>
                <div className="bg-white/40 rounded-2xl py-6 px-4 border border-black/[0.04] backdrop-blur-sm">
                  <p className="text-4xl font-mono font-bold text-gray-800 tracking-[0.3em]">
                    {pairingCode.code}
                  </p>
                </div>
                <p className="text-xs text-gray-400">
                  {tx('Gültig bis')}
                  {new Date(pairingCode.expires_at).toLocaleTimeString(tx.locale)}{' '}
                  {tx('(10 Minuten)')}
                </p>
                <button
                  onClick={() => {
                    setShowPairing(false);
                    loadData();
                  }}
                  className="w-full py-2.5 bg-black/[0.04] text-gray-600 rounded-xl text-sm font-medium hover:bg-black/[0.06] transition-colors"
                >
                  {tx('Schliessen')}
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Gateway List */}
      {gateways.length === 0 ? (
        <div className="glass-card p-12 text-center">
          <div className="text-4xl mb-4">🖥</div>
          <h3 className="text-lg font-semibold text-gray-800 mb-2">{tx('Keine Gateways')}</h3>
          <p className="text-sm text-gray-400 mb-4">
            {tx('Verbinde dein erstes Raspberry Pi Gateway mit einer Zone.')}
          </p>
        </div>
      ) : (
        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
          {gateways.map((gw) => (
            <div key={gw.id} className="glass-card accent-glow p-6">
              <div className="flex items-start justify-between mb-3">
                <div>
                  <h3 className="text-base font-semibold text-gray-800">
                    {gw.name || gw.hardware_id}
                  </h3>
                  <p className="text-xs text-gray-400 font-mono mt-0.5">{gw.hardware_id}</p>
                </div>
                <span
                  className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${
                    gw.status === 'online' && !gatewayHasWavIssue(gw)
                      ? 'bg-emerald-50 text-emerald-600'
                      : gw.status === 'online'
                        ? 'bg-amber-50 text-amber-700'
                        : 'bg-gray-100 text-gray-400'
                  }`}
                >
                  <span
                    className={`w-1.5 h-1.5 rounded-full ${gw.status !== 'online' ? 'bg-gray-300' : gatewayHasWavIssue(gw) ? 'bg-amber-500' : 'bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.4)]'}`}
                  />
                  {tx.status(gw.status)}
                </span>
              </div>

              {gatewayHasWavIssue(gw) && (
                <div className="mb-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
                  {tx('WAV-Rückstau:')}
                  {gw.wav_pending_files ?? 0} {tx('Dateien')}
                  {gw.wav_last_error_code ? ` · ${gw.wav_last_error_code}` : ''}
                </div>
              )}

              <div className="grid grid-cols-3 gap-2 text-sm">
                <div className="bg-white/40 rounded-xl px-2.5 py-1.5 border border-black/[0.03]">
                  <p className="text-gray-400 text-xs">{tx('Sensoren')}</p>
                  <p className="font-semibold text-gray-800">{gw.sensor_count}</p>
                </div>
                <div className="bg-white/40 rounded-xl px-2.5 py-1.5 border border-black/[0.03]">
                  <p className="text-gray-400 text-xs">{tx('IP')}</p>
                  <p className="font-semibold text-gray-800 text-xs font-mono truncate">
                    {gw.local_ip || '–'}
                  </p>
                </div>
                <div className="bg-white/40 rounded-xl px-2.5 py-1.5 border border-black/[0.03]">
                  <p className="text-gray-400 text-xs">{tx('Zone')}</p>
                  <p className="font-semibold text-gray-800 text-xs truncate">
                    {gw.zone_name || '–'}
                  </p>
                </div>
              </div>

              {gw.last_seen && (
                <p className="text-xs text-gray-400 mt-3">
                  {tx('Zuletzt gesehen:')}
                  {new Date(gw.last_seen).toLocaleString(tx.locale)}
                </p>
              )}
              <div className="mt-4 border-t border-gray-100 pt-3">
                <button
                  onClick={(e) => {
                    e.preventDefault();
                    removal.begin(gw);
                  }}
                  disabled={!canManage || removal.busy}
                  className="ml-2 flex items-center gap-1 p-1.5 text-gray-600 hover:text-red-600 hover:bg-red-50 rounded-lg transition-all shrink-0 disabled:opacity-50 disabled:cursor-not-allowed"
                  aria-label={tx('Gateway entfernen')}
                  title={
                    canManage
                      ? tx('Gateway entfernen')
                      : tx('Keine Berechtigung für diesen Vorgang.')
                  }
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
                    />
                  </svg>
                  <span>{tx('Gateway entfernen')}</span>
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <RemoveGatewayDialog
        gateway={removal.target}
        busy={removal.busy}
        error={removal.error}
        onCancel={removal.cancel}
        onConfirm={removal.confirm}
      />
    </div>
  );
}
