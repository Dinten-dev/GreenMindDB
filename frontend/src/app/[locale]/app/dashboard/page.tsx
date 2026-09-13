'use client';

import { useState, useEffect, useCallback } from 'react';
import Link from 'next/link';
import { useLocale } from 'next-intl';
import { useAuth } from '@/contexts/AuthContext';
import {
  apiListZones,
  apiListGateways,
  apiListSensors,
  apiDeleteGateway,
  apiUpdateSensor,
  gatewayHasWavIssue,
  Zone,
  GatewayInfo,
  SensorInfo,
} from '@/lib/api';
import PairSensorDialog from '../sensors/PairSensorDialog';

export default function DashboardPage() {
  const locale = useLocale();
  const [loadError, setLoadError] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const { user, createOrg, refresh } = useAuth();
  const [zones, setZones] = useState<Zone[]>([]);
  const [gateways, setGateways] = useState<GatewayInfo[]>([]);
  const [sensors, setSensors] = useState<SensorInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [orgName, setOrgName] = useState('');
  const [creatingOrg, setCreatingOrg] = useState(false);

  // Deletion states
  const [deletingGatewayId, setDeletingGatewayId] = useState<string | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [isPairDialogOpen, setIsPairDialogOpen] = useState(false);
  const [updatingSmsSensorId, setUpdatingSmsSensorId] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setLoadError(false);
    try {
      const [z, gw, sen] = await Promise.all([apiListZones(), apiListGateways(), apiListSensors()]);
      setZones(z);
      setGateways(gw);
      setSensors(sen);
      setUpdatedAt(new Date());
    } catch (err) {
      console.error('Dashboard load error:', err);
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (user?.organization_id) loadData();
    else setLoading(false);
  }, [user?.organization_id, loadData]);

  const handleCreateOrg = async () => {
    if (!orgName.trim()) return;
    setCreatingOrg(true);
    setActionError(null);
    try {
      await createOrg(orgName);
      await refresh();
    } catch (err) {
      setActionError('Die Organisation konnte nicht erstellt werden. Bitte erneut versuchen.');
      console.error(err);
    } finally {
      setCreatingOrg(false);
    }
  };

  const confirmDelete = async () => {
    if (!deletingGatewayId) return;
    setIsDeleting(true);
    setActionError(null);
    try {
      await apiDeleteGateway(deletingGatewayId);
      setGateways((prev) => prev.filter((g) => g.id !== deletingGatewayId));
      // Reload sensors to reflect cascade delete
      const sen = await apiListSensors();
      setSensors(sen);
      setDeletingGatewayId(null);
    } catch (err) {
      console.error('Failed to delete gateway:', err);
      setActionError('Das Gateway konnte nicht vollständig entfernt werden. Bitte erneut laden.');
    } finally {
      setIsDeleting(false);
    }
  };

  const toggleSmsAlerts = async (sensor: SensorInfo) => {
    setUpdatingSmsSensorId(sensor.id);
    const newValue = !sensor.sms_alerts_enabled;
    try {
      const updated = await apiUpdateSensor(sensor.id, { sms_alerts_enabled: newValue });
      setSensors((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
    } catch (err) {
      console.error('Failed to update SMS alerts:', err);
      alert('Fehler beim Aktualisieren der SMS-Einstellungen.');
    } finally {
      setUpdatingSmsSensorId(null);
    }
  };

  // ── No Organization ──
  if (!loading && !user?.organization_id) {
    return (
      <div className="max-w-md mx-auto mt-24">
        <div className="glass-card p-8 text-center">
          <h2 className="text-xl font-semibold text-gray-800 mb-2">Willkommen bei GreenMind</h2>
          <p className="text-sm text-gray-400 mb-6">
            Eine Organisation gruppiert Zonen, Gateways und Teammitglieder.
          </p>
          {actionError && (
            <p role="alert" className="text-sm text-red-700 mb-4">
              {actionError}
            </p>
          )}
          <label htmlFor="organization-name" className="block text-left text-sm text-gray-600 mb-2">
            Name deiner Organisation
          </label>
          <input
            id="organization-name"
            type="text"
            value={orgName}
            onChange={(e) => setOrgName(e.target.value)}
            className="w-full px-4 py-2.5 rounded-xl bg-white/60 border border-black/[0.06] text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-emerald-500/30 focus:border-emerald-500/30 backdrop-blur-sm mb-4"
            placeholder="Name der Organisation"
          />
          <button
            onClick={handleCreateOrg}
            disabled={creatingOrg || !orgName.trim()}
            className="w-full py-2.5 bg-gradient-to-r from-emerald-500 to-emerald-600 text-white rounded-xl font-medium text-sm hover:from-emerald-600 hover:to-emerald-700 transition-all duration-200 disabled:opacity-50 shadow-sm"
          >
            {creatingOrg ? 'Erstelle…' : 'Organisation erstellen'}
          </button>
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="animate-pulse space-y-6">
        <div className="h-8 w-48 bg-black/[0.04] rounded-xl" />
        <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="h-28 bg-black/[0.04] rounded-2xl" />
          ))}
        </div>
        <div className="h-80 bg-black/[0.04] rounded-2xl" />
      </div>
    );
  }

  if (loadError)
    return (
      <div className="glass-card p-8 max-w-xl">
        <h1 className="text-2xl font-semibold text-gray-800">Überblick</h1>
        <p role="alert" className="mt-3 text-sm text-gray-600">
          Deine Daten konnten nicht geladen werden. Bitte prüfe die Verbindung und versuche es
          erneut.
        </p>
        <button
          onClick={loadData}
          className="mt-5 px-5 py-3 rounded-xl bg-emerald-600 text-white text-sm font-medium"
        >
          Erneut laden
        </button>
      </div>
    );

  const onlineGateways = gateways.filter((g) => g.status === 'online').length;
  const healthyDataFlows = gateways.filter(
    (g) => g.status === 'online' && !gatewayHasWavIssue(g)
  ).length;

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="dashboard-welcome border border-gray-200/70 rounded-2xl p-6 sm:p-8 flex flex-col lg:flex-row lg:items-center justify-between gap-5">
        <div>
          <h1 className="text-2xl font-bold text-gray-800 tracking-tight">
            Dein Gewächshaus im Blick
          </h1>
          <p className="text-sm text-gray-500 mt-2">
            {user?.organization_name || 'Deine Organisation'} – Überblick
          </p>
        </div>
        <div className="flex flex-wrap gap-3">
          <Link
            href={`/${locale}/app/sensors`}
            className="px-5 py-3 rounded-xl bg-emerald-600 text-white text-sm font-medium hover:bg-emerald-700"
          >
            Messungen ansehen
          </Link>
          <button
            onClick={() => setIsPairDialogOpen(true)}
            className="flex items-center justify-center gap-2 px-5 py-3 bg-white border border-gray-200 text-gray-700 text-sm font-medium rounded-xl hover:bg-gray-50 whitespace-nowrap"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M12 4v16m8-8H4"
              />
            </svg>
            Sensor verbinden
          </button>
        </div>
      </div>

      {actionError && (
        <p role="alert" className="rounded-xl p-4 bg-red-50 text-red-700 text-sm">
          {actionError}
        </p>
      )}
      <div className="flex flex-wrap justify-between items-center gap-3 text-sm text-gray-500">
        <p>
          {updatedAt
            ? `Zuletzt geladen: ${updatedAt.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`
            : 'Noch nicht geladen'}
        </p>
        <button
          onClick={loadData}
          className="px-3 py-2 rounded-lg border border-gray-200 bg-white hover:bg-gray-50 text-gray-700"
        >
          Aktualisieren
        </button>
      </div>
      {gateways.some((g) => g.status !== 'online' || gatewayHasWavIssue(g)) && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-5 py-4">
          <p className="font-medium text-sm text-amber-900">Verbindungen prüfen</p>
          <p className="text-sm text-amber-800 mt-1">
            Mindestens ein Gateway ist offline oder meldet ausstehende Übertragungen.
          </p>
          <Link
            href={`/${locale}/app/gateways`}
            className="inline-block mt-2 text-sm font-medium text-amber-900 underline underline-offset-4"
          >
            Gateways ansehen →
          </Link>
        </div>
      )}

      {/* Stat Cards */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
        <StatCard label="Zonen" value={zones.length} />
        <StatCard label="Gateways" value={gateways.length} sub={`${onlineGateways} online`} />
        <StatCard label="Sensoren" value={sensors.length} />
        <StatCard
          label="Übertragung bereit"
          value={healthyDataFlows}
          sub={`von ${gateways.length} Gateways online, ohne WAV-Warnung`}
          accent
        />
      </div>

      {/* Gateway Status */}
      {gateways.length > 0 ? (
        <div className="glass-card p-4 sm:p-6">
          <div className="flex flex-wrap justify-between items-center gap-3 mb-4">
            <div>
              <h2 className="text-lg font-semibold text-gray-800">Verbindungen</h2>
              <p className="text-sm text-gray-500 mt-1">Deine Gateways übertragen die Messungen.</p>
            </div>
            <Link href={`/${locale}/app/gateways`} className="text-sm text-emerald-700 font-medium">
              Alle Gateways →
            </Link>
          </div>
          <div className="grid xl:grid-cols-2 gap-3">
            {gateways.map((gw) => {
              const wavIssue = gatewayHasWavIssue(gw);
              return (
                <div
                  key={gw.id}
                  className={`group flex items-center gap-3 px-4 py-3 bg-white/40 rounded-xl border transition-colors hover:bg-white/60 relative ${wavIssue ? 'border-amber-300' : 'border-black/[0.03]'}`}
                >
                  <span
                    className={`w-2.5 h-2.5 shrink-0 rounded-full ${gw.status !== 'online' ? 'bg-gray-300' : wavIssue ? 'bg-amber-500' : 'bg-emerald-500'}`}
                  />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-gray-800 truncate">
                      {gw.name || gw.hardware_id}
                    </p>
                    <p className="text-xs text-gray-400">
                      {gw.status === 'online' ? 'Online' : 'Offline'} · {gw.sensor_count} Sensoren ·{' '}
                      {gw.zone_name}
                    </p>
                    {wavIssue && (
                      <p className="text-xs text-amber-600">
                        Ausstehende WAV-Dateien: {gw.wav_pending_files ?? 0}
                        {gw.wav_last_error_code ? ` · ${gw.wav_last_error_code}` : ''}
                      </p>
                    )}
                    <p className="text-xs text-gray-500 mt-1">
                      {gw.last_seen
                        ? `Letzter Kontakt ${timeAgo(gw.last_seen)}`
                        : 'Noch kein Kontakt'}
                    </p>
                  </div>
                  <button
                    onClick={() => setDeletingGatewayId(gw.id)}
                    className="shrink-0 p-2.5 text-gray-500 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors"
                    aria-label={`${gw.name || gw.hardware_id} entfernen`}
                    title="Gateway entfernen"
                  >
                    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={2}
                        d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
                      />
                    </svg>
                  </button>
                </div>
              );
            })}
          </div>
        </div>
      ) : (
        <div className="glass-card p-12 text-center">
          <h3 className="text-lg font-semibold text-gray-800 mb-2">Noch keine Gateways</h3>
          <p className="text-sm text-gray-400">
            Verbinde dein erstes Gateway, damit die Messungen deiner Sensoren hier ankommen.
          </p>
          <Link
            href={`/${locale}/app/${zones.length ? 'gateways' : 'zones'}`}
            className="inline-block mt-5 px-5 py-3 rounded-xl bg-emerald-600 text-white text-sm font-medium"
          >
            {zones.length ? 'Gateway verbinden' : 'Erste Zone erstellen'}
          </Link>
        </div>
      )}

      {/* Sensor Status & SMS Alerts */}
      {sensors.length > 0 && (
        <div className="glass-card p-4 sm:p-6">
          <h2 className="text-lg font-semibold text-gray-800 mb-4">Sensoren & SMS-Warnungen</h2>
          <div className="grid xl:grid-cols-2 gap-3">
            {sensors.map((sensor) => (
              <div
                key={sensor.id}
                className="flex items-center justify-between gap-3 px-4 py-3 bg-white/40 rounded-xl border border-black/[0.03]"
              >
                <div className="flex items-center gap-3 min-w-0">
                  <span
                    className={`w-2.5 h-2.5 rounded-full shrink-0 ${sensor.status === 'online' ? 'bg-emerald-500' : 'bg-gray-300'}`}
                  />
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-gray-800 truncate">
                      {sensor.name || sensor.mac_address}
                    </p>
                    <p className="text-xs text-gray-500 truncate">
                      {sensor.status === 'online' ? 'Online' : 'Offline'} · {sensor.mac_address}
                    </p>
                  </div>
                </div>
                <button
                  onClick={() => toggleSmsAlerts(sensor)}
                  disabled={updatingSmsSensorId === sensor.id}
                  className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg transition-all border shrink-0 disabled:opacity-50 ${
                    sensor.sms_alerts_enabled
                      ? 'bg-emerald-50 text-emerald-700 border-emerald-200 hover:bg-emerald-100'
                      : 'bg-gray-50 text-gray-500 border-gray-200 hover:bg-gray-100'
                  }`}
                  aria-pressed={sensor.sms_alerts_enabled}
                  aria-label={`SMS-Warnungen für ${sensor.name || sensor.mac_address}`}
                  title="SMS Warnungen bei Elektroden-Abfall"
                >
                  <span className="text-sm">📱</span>
                  {updatingSmsSensorId === sensor.id
                    ? '...'
                    : sensor.sms_alerts_enabled
                      ? 'SMS aktiv'
                      : 'SMS stumm'}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Delete Confirmation Modal */}
      {deletingGatewayId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div
            className="absolute inset-0 bg-black/20 backdrop-blur-sm"
            onClick={() => setDeletingGatewayId(null)}
          />
          <div className="relative bg-white/80 rounded-2xl border border-white/50 shadow-2xl backdrop-blur-xl p-6 max-w-sm w-full animate-in fade-in zoom-in duration-200">
            <div className="w-12 h-12 rounded-full bg-red-50 text-red-500 flex items-center justify-center mb-4 mx-auto">
              <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
                />
              </svg>
            </div>
            <h3 className="text-lg font-semibold text-center text-gray-800 mb-2">
              Gateway entfernen?
            </h3>
            <p className="text-sm text-center text-gray-500 mb-6">
              Bist du sicher, dass du dieses Gateway löschen möchtest? Alle zugehörigen Sensoren und
              Daten werden ebenfalls entfernt. Dies kann nicht rückgängig gemacht werden.
            </p>
            <div className="flex gap-3">
              <button
                onClick={() => setDeletingGatewayId(null)}
                className="flex-1 px-4 py-2.5 bg-white border border-gray-200 text-gray-700 rounded-xl text-sm font-medium hover:bg-gray-50 transition-colors"
              >
                Abbrechen
              </button>
              <button
                onClick={confirmDelete}
                disabled={isDeleting}
                className="flex-1 px-4 py-2.5 bg-red-500 text-white rounded-xl text-sm font-medium hover:bg-red-600 transition-colors disabled:opacity-50"
              >
                {isDeleting ? 'Lösche...' : 'Löschen'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Pair Sensor Dialog */}
      <PairSensorDialog
        isOpen={isPairDialogOpen}
        onClose={() => setIsPairDialogOpen(false)}
        onSuccess={() => loadData()}
      />
    </div>
  );
}

function StatCard({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: number;
  sub?: string;
  accent?: boolean;
}) {
  return (
    <div className={`glass-card min-w-0 p-4 sm:p-5 ${accent ? 'border-emerald-500/20' : ''}`}>
      <div className="flex items-center gap-2 mb-3">
        <span className="text-sm text-gray-600 font-medium">{label}</span>
      </div>
      <p
        className={`text-2xl font-bold tracking-tight ${accent ? 'text-emerald-600' : 'text-gray-800'}`}
      >
        {value.toLocaleString()}
      </p>
      {sub && <p className="text-xs text-gray-400 mt-1">{sub}</p>}
    </div>
  );
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return `vor ${sec}s`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `vor ${min}m`;
  const hrs = Math.floor(min / 60);
  if (hrs < 24) return `vor ${hrs}h`;
  return `vor ${Math.floor(hrs / 24)}d`;
}
