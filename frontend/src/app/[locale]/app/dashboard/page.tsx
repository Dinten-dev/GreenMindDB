'use client';

import { useAppText } from '@/i18n/app-text';
import { useGatewayRemoval } from '@/hooks/useGatewayRemoval';
import RemoveGatewayDialog, { GatewayRemovalNotice } from '@/components/RemoveGatewayDialog';

import { useState, useEffect, useCallback, useRef } from 'react';
import Link from 'next/link';
import { useLocale } from 'next-intl';
import { useAuth } from '@/contexts/AuthContext';
import {
  apiListZones,
  apiListGateways,
  apiListSensors,
  apiUpdateSensor,
  gatewayHasWavIssue,
  Zone,
  GatewayInfo,
  SensorInfo,
} from '@/lib/api';
import PairSensorDialog from '../sensors/PairSensorDialog';

export default function DashboardPage() {
  const tx = useAppText();
  const removedIds = useRef(new Set<string>());

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

  const [isPairDialogOpen, setIsPairDialogOpen] = useState(false);
  const [updatingSmsSensorId, setUpdatingSmsSensorId] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    setLoading(true);
    setLoadError(false);
    try {
      const [z, gw, sen] = await Promise.all([apiListZones(), apiListGateways(), apiListSensors()]);
      setZones(z);
      setGateways(gw.filter((gateway) => !removedIds.current.has(gateway.id)));
      setSensors(
        sen.map((sensor) =>
          removedIds.current.has(sensor.gateway_id) ? { ...sensor, status: 'disconnected' } : sensor
        )
      );
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
      setActionError(tx('Die Organisation konnte nicht erstellt werden. Bitte erneut versuchen.'));
      console.error(err);
    } finally {
      setCreatingOrg(false);
    }
  };

  const canManage = user?.role === 'owner' || user?.role === 'admin';
  const removal = useGatewayRemoval({
    onRemoved: (id) => {
      removedIds.current.add(id);
      setGateways((previous) => previous.filter((gateway) => gateway.id !== id));
      setSensors((previous) =>
        previous.map((sensor) =>
          sensor.gateway_id === id ? { ...sensor, status: 'disconnected' } : sensor
        )
      );
      setZones((previous) =>
        previous.map((zone) => ({
          ...zone,
          gateway_count: Math.max(
            0,
            zone.gateway_count -
              (gateways.find((gateway) => gateway.id === id)?.zone_id === zone.id ? 1 : 0)
          ),
        }))
      );
    },
    refresh: async () => {
      const [nextZones, nextGateways, nextSensors] = await Promise.all([
        apiListZones(),
        apiListGateways(),
        apiListSensors(),
      ]);
      setZones(nextZones);
      setGateways(nextGateways.filter((gateway) => !removedIds.current.has(gateway.id)));
      setSensors(
        nextSensors.map((sensor) =>
          removedIds.current.has(sensor.gateway_id) ? { ...sensor, status: 'disconnected' } : sensor
        )
      );
      setUpdatedAt(new Date());
    },
  });

  const toggleSmsAlerts = async (sensor: SensorInfo) => {
    setUpdatingSmsSensorId(sensor.id);
    const newValue = !sensor.sms_alerts_enabled;
    try {
      const updated = await apiUpdateSensor(sensor.id, { sms_alerts_enabled: newValue });
      setSensors((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
    } catch (err) {
      console.error('Failed to update SMS alerts:', err);
      alert(tx('Fehler beim Aktualisieren der SMS-Einstellungen.'));
    } finally {
      setUpdatingSmsSensorId(null);
    }
  };

  // ── No Organization ──
  if (!loading && !user?.organization_id) {
    return (
      <div className="max-w-md mx-auto mt-24">
        <div className="glass-card p-8 text-center">
          <h2 className="text-xl font-semibold text-gray-800 mb-2">
            {tx('Willkommen bei GreenMind')}
          </h2>
          <p className="text-sm text-gray-400 mb-6">
            {tx('Eine Organisation gruppiert Zonen, Gateways und Teammitglieder.')}
          </p>
          {actionError && (
            <p role="alert" className="text-sm text-red-700 mb-4">
              {actionError}
            </p>
          )}
          <label htmlFor="organization-name" className="block text-left text-sm text-gray-600 mb-2">
            {tx('Name deiner Organisation')}
          </label>
          <input
            id="organization-name"
            type="text"
            value={orgName}
            onChange={(e) => setOrgName(e.target.value)}
            className="w-full px-4 py-2.5 rounded-xl bg-white/60 border border-black/[0.06] text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-emerald-500/30 focus:border-emerald-500/30 backdrop-blur-sm mb-4"
            placeholder={tx('Name der Organisation')}
          />
          <button
            onClick={handleCreateOrg}
            disabled={creatingOrg || !orgName.trim()}
            className="w-full py-2.5 bg-gradient-to-r from-emerald-500 to-emerald-600 text-white rounded-xl font-medium text-sm hover:from-emerald-600 hover:to-emerald-700 transition-all duration-200 disabled:opacity-50 shadow-sm"
          >
            {creatingOrg ? tx('Erstelle…') : tx('Organisation erstellen')}
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
        <h1 className="text-2xl font-semibold text-gray-800">{tx('Überblick')}</h1>
        <p role="alert" className="mt-3 text-sm text-gray-600">
          {tx(
            'Deine Daten konnten nicht geladen werden. Bitte prüfe die Verbindung und versuche es erneut.'
          )}
        </p>
        <button
          onClick={loadData}
          className="mt-5 px-5 py-3 rounded-xl bg-emerald-600 text-white text-sm font-medium"
        >
          {tx('Erneut laden')}
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
            {tx('Dein Gewächshaus im Blick')}
          </h1>
          <p className="text-sm text-gray-500 mt-2">
            {user?.organization_name || tx('Deine Organisation')} {tx('– Überblick')}
          </p>
        </div>
        <div className="flex flex-wrap gap-3">
          <Link
            href={`/${locale}/app/sensors`}
            className="px-5 py-3 rounded-xl bg-emerald-600 text-white text-sm font-medium hover:bg-emerald-700"
          >
            {tx('Messungen ansehen')}
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
            {tx('Sensor verbinden')}
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
            ? tx('Zuletzt geladen: {value0}', {
                value0: updatedAt.toLocaleTimeString(locale, {
                  hour: '2-digit',
                  minute: '2-digit',
                  second: '2-digit',
                }),
              })
            : tx('Noch nicht geladen')}
        </p>
        <button
          onClick={loadData}
          className="px-3 py-2 rounded-lg border border-gray-200 bg-white hover:bg-gray-50 text-gray-700"
        >
          {tx('Aktualisieren')}
        </button>
      </div>
      {gateways.some((g) => g.status !== 'online' || gatewayHasWavIssue(g)) && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-5 py-4">
          <p className="font-medium text-sm text-amber-900">{tx('Verbindungen prüfen')}</p>
          <p className="text-sm text-amber-800 mt-1">
            {tx('Mindestens ein Gateway ist offline oder meldet ausstehende Übertragungen.')}
          </p>
          <Link
            href={`/${locale}/app/gateways`}
            className="inline-block mt-2 text-sm font-medium text-amber-900 underline underline-offset-4"
          >
            {tx('Gateways ansehen →')}
          </Link>
        </div>
      )}

      <GatewayRemovalNotice notice={removal.notice} busy={removal.busy} onRetry={removal.retry} />
      {/* Stat Cards */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
        <StatCard label={tx('Zonen')} value={zones.length} />
        <StatCard
          label={tx('Gateways')}
          value={gateways.length}
          sub={tx('{count} online', { count: onlineGateways })}
        />
        <StatCard label={tx('Sensoren')} value={sensors.length} />
        <StatCard
          label={tx('Übertragung bereit')}
          value={healthyDataFlows}
          sub={tx('von {count} Gateways online, ohne WAV-Warnung', { count: gateways.length })}
          accent
        />
      </div>

      {/* Gateway Status */}
      {gateways.length > 0 ? (
        <div className="glass-card p-4 sm:p-6">
          <div className="flex flex-wrap justify-between items-center gap-3 mb-4">
            <div>
              <h2 className="text-lg font-semibold text-gray-800">{tx('Verbindungen')}</h2>
              <p className="text-sm text-gray-500 mt-1">
                {tx('Deine Gateways übertragen die Messungen.')}
              </p>
            </div>
            <Link href={`/${locale}/app/gateways`} className="text-sm text-emerald-700 font-medium">
              {tx('Alle Gateways →')}
            </Link>
          </div>
          <div className="grid xl:grid-cols-2 gap-3">
            {gateways.map((gw) => {
              const wavIssue = gatewayHasWavIssue(gw);
              return (
                <div
                  key={gw.id}
                  className={`group grid grid-cols-[auto_1fr] sm:grid-cols-[auto_1fr_auto] items-center gap-3 px-4 py-3 bg-white/40 rounded-xl border transition-colors hover:bg-white/60 relative ${wavIssue ? 'border-amber-300' : 'border-black/[0.03]'}`}
                >
                  <span
                    className={`w-2.5 h-2.5 shrink-0 rounded-full ${gw.status !== 'online' ? 'bg-gray-300' : wavIssue ? 'bg-amber-500' : 'bg-emerald-500'}`}
                  />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-gray-800 truncate">
                      {gw.name || gw.hardware_id}
                    </p>
                    <p className="text-xs text-gray-400">
                      {gw.status === 'online' ? tx('Online') : tx('Offline')} · {gw.sensor_count}{' '}
                      {tx('Sensoren ·')} {gw.zone_name}
                    </p>
                    {wavIssue && (
                      <p className="text-xs text-amber-600">
                        {tx('Ausstehende WAV-Dateien:')}
                        {gw.wav_pending_files ?? 0}
                        {gw.wav_last_error_code ? ` · ${gw.wav_last_error_code}` : ''}
                      </p>
                    )}
                    <p className="text-xs text-gray-500 mt-1">
                      {gw.last_seen
                        ? tx('Letzter Kontakt {value0}', {
                            value0: timeAgo(gw.last_seen, tx.locale),
                          })
                        : tx('Noch kein Kontakt')}
                    </p>
                  </div>
                  <button
                    onClick={() => removal.begin(gw)}
                    disabled={!canManage || removal.busy}
                    className="col-span-2 sm:col-span-1 flex items-center justify-self-start gap-1 p-2.5 text-sm text-gray-600 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
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
              );
            })}
          </div>
        </div>
      ) : (
        <div className="glass-card p-12 text-center">
          <h3 className="text-lg font-semibold text-gray-800 mb-2">{tx('Noch keine Gateways')}</h3>
          <p className="text-sm text-gray-400">
            {tx('Verbinde dein erstes Gateway, damit die Messungen deiner Sensoren hier ankommen.')}
          </p>
          <Link
            href={`/${locale}/app/${zones.length ? 'gateways' : 'zones'}`}
            className="inline-block mt-5 px-5 py-3 rounded-xl bg-emerald-600 text-white text-sm font-medium"
          >
            {zones.length ? tx('Gateway verbinden') : tx('Erste Zone erstellen')}
          </Link>
        </div>
      )}

      {/* Sensor Status & SMS Alerts */}
      {sensors.length > 0 && (
        <div className="glass-card p-4 sm:p-6">
          <h2 className="text-lg font-semibold text-gray-800 mb-4">
            {tx('Sensoren & SMS-Warnungen')}
          </h2>
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
                      {sensor.status === 'disconnected'
                        ? tx('Getrennt')
                        : sensor.status === 'online'
                          ? tx('Online')
                          : tx('Offline')}{' '}
                      · {sensor.mac_address}
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
                  aria-label={tx('SMS-Warnungen für {value0}', {
                    value0: sensor.name || sensor.mac_address,
                  })}
                  title={tx('SMS Warnungen bei Elektroden-Abfall')}
                >
                  <span className="text-sm">📱</span>
                  {updatingSmsSensorId === sensor.id
                    ? '...'
                    : sensor.sms_alerts_enabled
                      ? tx('SMS aktiv')
                      : tx('SMS stumm')}
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      <PairSensorDialog
        isOpen={isPairDialogOpen}
        onClose={() => setIsPairDialogOpen(false)}
        onSuccess={() => loadData()}
      />
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
  const tx = useAppText();

  return (
    <div className={`glass-card min-w-0 p-4 sm:p-5 ${accent ? 'border-emerald-500/20' : ''}`}>
      <div className="flex items-center gap-2 mb-3">
        <span className="text-sm text-gray-600 font-medium">{tx(label)}</span>
      </div>
      <p
        className={`text-2xl font-bold tracking-tight ${accent ? 'text-emerald-600' : 'text-gray-800'}`}
      >
        {value.toLocaleString(tx.locale)}
      </p>
      {sub && <p className="text-xs text-gray-400 mt-1">{sub}</p>}
    </div>
  );
}

function timeAgo(iso: string, locale: string): string {
  const seconds = Math.floor((new Date(iso).getTime() - Date.now()) / 1000);
  const format = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' });
  if (Math.abs(seconds) < 60) return format.format(seconds, 'second');
  const minutes = Math.trunc(seconds / 60);
  if (Math.abs(minutes) < 60) return format.format(minutes, 'minute');
  const hours = Math.trunc(minutes / 60);
  if (Math.abs(hours) < 24) return format.format(hours, 'hour');
  return format.format(Math.trunc(hours / 24), 'day');
}
