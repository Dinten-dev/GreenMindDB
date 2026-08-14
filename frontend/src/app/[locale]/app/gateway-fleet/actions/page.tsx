'use client';

import { useEffect, useState, useCallback } from 'react';
import {
    getFleetOverview,
    issueCommand,
    setDesiredState,
    initiateRollback,
    startRollout,
    listAppReleases,
    type GatewayFleetItem,
    type GatewayAppRelease,
} from '@/lib/gateway-admin-api';

const COMMAND_TYPES = [
    { value: 'restart_gateway_service', label: '🔄 Restart Service', description: 'Gateway-Dienst neu starten' },
    { value: 'reload_gateway_config', label: '⚙ Reload Config', description: 'Konfiguration neu laden' },
    { value: 'enable_maintenance_mode', label: '⏸ Maintenance On', description: 'Wartungsmodus aktivieren' },
    { value: 'disable_maintenance_mode', label: '▶ Maintenance Off', description: 'Wartungsmodus beenden' },
    { value: 'controlled_reboot', label: '⚡ Reboot', description: 'Kontrollierter Neustart' },
];

export default function GatewayActionsPage() {
    const [gateways, setGateways] = useState<GatewayFleetItem[]>([]);
    const [releases, setReleases] = useState<GatewayAppRelease[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [success, setSuccess] = useState<string | null>(null);

    // Command form
    const [selectedGw, setSelectedGw] = useState('');
    const [selectedCmd, setSelectedCmd] = useState('restart_gateway_service');

    // Desired state form
    const [dsGateway, setDsGateway] = useState('');
    const [dsAppVersion, setDsAppVersion] = useState('');
    const [dsConfigVersion, setDsConfigVersion] = useState('');
    const [dsWindowStart, setDsWindowStart] = useState('');
    const [dsWindowEnd, setDsWindowEnd] = useState('');
    const [dsTimezone, setDsTimezone] = useState('UTC');
    const [dsRing, setDsRing] = useState('stable');

    // Rollout form
    const [rolloutVersion, setRolloutVersion] = useState('');
    const [rolloutRing, setRolloutRing] = useState('canary');

    const loadData = useCallback(async () => {
        try {
            setLoading(true);
            const [fleet, rels] = await Promise.all([
                getFleetOverview({ limit: 100 }),
                listAppReleases({ is_active: true, limit: 50 }),
            ]);
            setGateways(fleet.items);
            setReleases(rels.items);
            setError(null);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to load data');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { loadData(); }, [loadData]);

    const showSuccess = (msg: string) => {
        setSuccess(msg);
        setTimeout(() => setSuccess(null), 4000);
    };

    const handleCommand = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!selectedGw) return;
        try {
            await issueCommand(selectedGw, selectedCmd);
            showSuccess(`Befehl "${selectedCmd}" an Gateway gesendet`);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Command failed');
        }
    };

    const handleDesiredState = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!dsGateway) return;
        try {
            const updates: Record<string, unknown> = {};
            if (dsAppVersion) updates.desired_app_version = dsAppVersion;
            if (dsConfigVersion) updates.desired_config_version = dsConfigVersion;
            if (dsWindowStart) updates.update_window_start = dsWindowStart;
            if (dsWindowEnd) updates.update_window_end = dsWindowEnd;
            if (dsTimezone) updates.update_timezone = dsTimezone;
            if (dsRing) updates.rollout_ring = dsRing;

            await setDesiredState(dsGateway, updates);
            showSuccess('Desired State aktualisiert');
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Update failed');
        }
    };

    const handleRollout = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!rolloutVersion) return;
        try {
            const result = await startRollout({
                release_version: rolloutVersion,
                target_ring: rolloutRing,
            });
            showSuccess(`Rollout gestartet: ${result.gateways_updated} Gateways`);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Rollout failed');
        }
    };

    const handleRollback = async (gatewayId: string) => {
        if (!confirm('Rollback zum vorherigen Release?')) return;
        try {
            await initiateRollback(gatewayId);
            showSuccess('Rollback initiiert');
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Rollback failed');
        }
    };

    if (loading) {
        return <div className="glass-card rounded-2xl p-12 text-center text-gray-400">Lade…</div>;
    }

    return (
        <div className="space-y-8">
            {/* Alerts */}
            {success && (
                <div className="bg-emerald-50 border border-emerald-200 text-emerald-700 px-4 py-3 rounded-xl text-sm">
                    ✓ {success}
                </div>
            )}
            {error && (
                <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-xl text-sm">
                    {error}
                    <button onClick={() => setError(null)} className="ml-2 text-red-400 hover:text-red-600">✕</button>
                </div>
            )}

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {/* Remote Command */}
                <form onSubmit={handleCommand} className="glass-card rounded-2xl p-6 space-y-4">
                    <h3 className="font-semibold text-gray-800">Remote Command</h3>
                    <div>
                        <label className="block text-xs text-gray-500 mb-1">Gateway</label>
                        <select
                            value={selectedGw}
                            onChange={(e) => setSelectedGw(e.target.value)}
                            className="w-full px-3 py-2 rounded-xl border border-black/[0.08] text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400/40"
                            required
                            id="cmd-gateway-select"
                        >
                            <option value="">Gateway wählen…</option>
                            {gateways.map((gw) => (
                                <option key={gw.id} value={gw.id}>
                                    {gw.name || gw.hardware_id} ({gw.status})
                                </option>
                            ))}
                        </select>
                    </div>
                    <div>
                        <label className="block text-xs text-gray-500 mb-1">Befehl</label>
                        <select
                            value={selectedCmd}
                            onChange={(e) => setSelectedCmd(e.target.value)}
                            className="w-full px-3 py-2 rounded-xl border border-black/[0.08] text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400/40"
                            id="cmd-type-select"
                        >
                            {COMMAND_TYPES.map((cmd) => (
                                <option key={cmd.value} value={cmd.value}>
                                    {cmd.label} — {cmd.description}
                                </option>
                            ))}
                        </select>
                    </div>
                    <button
                        type="submit"
                        disabled={!selectedGw}
                        className="px-6 py-2.5 rounded-xl text-sm font-medium bg-blue-500 text-white hover:bg-blue-600 disabled:opacity-50 transition-colors shadow-sm"
                    >
                        Senden
                    </button>
                </form>

                {/* Rollout */}
                <form onSubmit={handleRollout} className="glass-card rounded-2xl p-6 space-y-4">
                    <h3 className="font-semibold text-gray-800">Staged Rollout</h3>
                    <div>
                        <label className="block text-xs text-gray-500 mb-1">Release</label>
                        <select
                            value={rolloutVersion}
                            onChange={(e) => setRolloutVersion(e.target.value)}
                            className="w-full px-3 py-2 rounded-xl border border-black/[0.08] text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400/40"
                            required
                            id="rollout-version-select"
                        >
                            <option value="">Release wählen…</option>
                            {releases.map((r) => (
                                <option key={r.id} value={r.version}>
                                    {r.version} ({r.channel})
                                </option>
                            ))}
                        </select>
                    </div>
                    <div>
                        <label className="block text-xs text-gray-500 mb-1">Ziel-Ring</label>
                        <select
                            value={rolloutRing}
                            onChange={(e) => setRolloutRing(e.target.value)}
                            className="w-full px-3 py-2 rounded-xl border border-black/[0.08] text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400/40"
                            id="rollout-ring-select"
                        >
                            <option value="canary">canary (Test-Gateways)</option>
                            <option value="early">early (Frühe Adoption)</option>
                            <option value="stable">stable (Alle)</option>
                            <option value="all">all (Alle erzwingen)</option>
                        </select>
                    </div>
                    <button
                        type="submit"
                        disabled={!rolloutVersion}
                        className="px-6 py-2.5 rounded-xl text-sm font-medium bg-indigo-500 text-white hover:bg-indigo-600 disabled:opacity-50 transition-colors shadow-sm"
                    >
                        Rollout starten
                    </button>
                </form>
            </div>

            {/* Desired State */}
            <form onSubmit={handleDesiredState} className="glass-card rounded-2xl p-6 space-y-4">
                <h3 className="font-semibold text-gray-800">Desired State setzen</h3>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                    <div>
                        <label className="block text-xs text-gray-500 mb-1">Gateway</label>
                        <select
                            value={dsGateway}
                            onChange={(e) => setDsGateway(e.target.value)}
                            className="w-full px-3 py-2 rounded-xl border border-black/[0.08] text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400/40"
                            required
                            id="ds-gateway-select"
                        >
                            <option value="">Gateway wählen…</option>
                            {gateways.map((gw) => (
                                <option key={gw.id} value={gw.id}>
                                    {gw.name || gw.hardware_id}
                                </option>
                            ))}
                        </select>
                    </div>
                    <div>
                        <label className="block text-xs text-gray-500 mb-1">App Version</label>
                        <select
                            value={dsAppVersion}
                            onChange={(e) => setDsAppVersion(e.target.value)}
                            className="w-full px-3 py-2 rounded-xl border border-black/[0.08] text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400/40"
                        >
                            <option value="">Nicht ändern</option>
                            {releases.map((r) => (
                                <option key={r.id} value={r.version}>{r.version}</option>
                            ))}
                        </select>
                    </div>
                    <div>
                        <label className="block text-xs text-gray-500 mb-1">Config Version</label>
                        <input
                            type="text"
                            value={dsConfigVersion}
                            onChange={(e) => setDsConfigVersion(e.target.value)}
                            placeholder="v3"
                            className="w-full px-3 py-2 rounded-xl border border-black/[0.08] text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400/40"
                        />
                    </div>
                    <div>
                        <label className="block text-xs text-gray-500 mb-1">Rollout Ring</label>
                        <select
                            value={dsRing}
                            onChange={(e) => setDsRing(e.target.value)}
                            className="w-full px-3 py-2 rounded-xl border border-black/[0.08] text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400/40"
                        >
                            <option value="canary">canary</option>
                            <option value="early">early</option>
                            <option value="stable">stable</option>
                        </select>
                    </div>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                    <div>
                        <label className="block text-xs text-gray-500 mb-1">Update Window Start</label>
                        <input
                            type="text"
                            value={dsWindowStart}
                            onChange={(e) => setDsWindowStart(e.target.value)}
                            placeholder="02:00"
                            className="w-full px-3 py-2 rounded-xl border border-black/[0.08] text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400/40"
                        />
                    </div>
                    <div>
                        <label className="block text-xs text-gray-500 mb-1">Update Window End</label>
                        <input
                            type="text"
                            value={dsWindowEnd}
                            onChange={(e) => setDsWindowEnd(e.target.value)}
                            placeholder="04:00"
                            className="w-full px-3 py-2 rounded-xl border border-black/[0.08] text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400/40"
                        />
                    </div>
                    <div>
                        <label className="block text-xs text-gray-500 mb-1">Timezone</label>
                        <select
                            value={dsTimezone}
                            onChange={(e) => setDsTimezone(e.target.value)}
                            className="w-full px-3 py-2 rounded-xl border border-black/[0.08] text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400/40"
                        >
                            <option value="UTC">UTC</option>
                            <option value="Europe/Zurich">Europe/Zurich</option>
                            <option value="Europe/Berlin">Europe/Berlin</option>
                        </select>
                    </div>
                </div>
                <button
                    type="submit"
                    disabled={!dsGateway}
                    className="px-6 py-2.5 rounded-xl text-sm font-medium bg-emerald-500 text-white hover:bg-emerald-600 disabled:opacity-50 transition-colors shadow-sm"
                >
                    Desired State setzen
                </button>
            </form>

            {/* Quick Rollback */}
            <div className="glass-card rounded-2xl p-6 space-y-4">
                <h3 className="font-semibold text-gray-800">Quick Rollback</h3>
                <p className="text-xs text-gray-400">Klicke auf ein Gateway, um sofort zur vorherigen Version zurückzukehren.</p>
                <div className="flex flex-wrap gap-2">
                    {gateways.map((gw) => (
                        <button
                            key={gw.id}
                            onClick={() => handleRollback(gw.id)}
                            className="px-3 py-1.5 rounded-xl text-xs border border-black/[0.08] hover:bg-red-50 hover:border-red-200 hover:text-red-600 transition-colors"
                        >
                            ↩ {gw.name || gw.hardware_id}
                        </button>
                    ))}
                </div>
            </div>
        </div>
    );
}
