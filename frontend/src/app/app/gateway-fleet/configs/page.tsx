'use client';

import { useEffect, useState, useCallback } from 'react';
import {
    listConfigReleases,
    uploadConfigRelease,
    toggleConfigRelease,
    type GatewayConfigRelease,
} from '@/lib/gateway-admin-api';

export default function GatewayConfigsPage() {
    const [configs, setConfigs] = useState<GatewayConfigRelease[]>([]);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [showCreate, setShowCreate] = useState(false);
    const [creating, setCreating] = useState(false);
    const [expanded, setExpanded] = useState<string | null>(null);

    // Create form
    const [version, setVersion] = useState('');
    const [configJson, setConfigJson] = useState('{\n  "upload_interval": 30,\n  "log_level": "INFO"\n}');
    const [compatMin, setCompatMin] = useState('');
    const [compatMax, setCompatMax] = useState('');

    const loadConfigs = useCallback(async () => {
        try {
            setLoading(true);
            const data = await listConfigReleases({ limit: 50 });
            setConfigs(data.items);
            setTotal(data.total);
            setError(null);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to load configs');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { loadConfigs(); }, [loadConfigs]);

    const handleCreate = async (e: React.FormEvent) => {
        e.preventDefault();
        setCreating(true);
        try {
            const payload = JSON.parse(configJson);
            await uploadConfigRelease({
                version,
                config_payload: payload,
                compatible_app_min: compatMin || undefined,
                compatible_app_max: compatMax || undefined,
            });
            setShowCreate(false);
            setVersion('');
            setConfigJson('{\n  "upload_interval": 30,\n  "log_level": "INFO"\n}');
            loadConfigs();
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Create failed');
        } finally {
            setCreating(false);
        }
    };

    const handleToggle = async (id: string, active: boolean) => {
        try {
            await toggleConfigRelease(id, active);
            loadConfigs();
        } catch (err) {
            alert(err instanceof Error ? err.message : 'Toggle failed');
        }
    };

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h2 className="text-lg font-semibold text-gray-800">Gateway Configs</h2>
                    <p className="text-xs text-gray-400 mt-0.5">{total} Config Releases</p>
                </div>
                <button
                    onClick={() => setShowCreate(!showCreate)}
                    className="px-4 py-2 rounded-xl text-sm font-medium bg-emerald-500 text-white hover:bg-emerald-600 transition-colors shadow-sm"
                    id="create-config-btn"
                >
                    + Config erstellen
                </button>
            </div>

            {showCreate && (
                <form onSubmit={handleCreate} className="glass-card rounded-2xl p-6 space-y-4">
                    <h3 className="font-semibold text-gray-800">Neue Config Version</h3>
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                        <div>
                            <label className="block text-xs text-gray-500 mb-1">Version</label>
                            <input
                                type="text"
                                value={version}
                                onChange={(e) => setVersion(e.target.value)}
                                placeholder="v3"
                                className="w-full px-3 py-2 rounded-xl border border-black/[0.08] text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400/40"
                                required
                                id="config-version-input"
                            />
                        </div>
                        <div>
                            <label className="block text-xs text-gray-500 mb-1">Kompatibel ab (App)</label>
                            <input
                                type="text"
                                value={compatMin}
                                onChange={(e) => setCompatMin(e.target.value)}
                                placeholder="1.0.0"
                                className="w-full px-3 py-2 rounded-xl border border-black/[0.08] text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400/40"
                            />
                        </div>
                        <div>
                            <label className="block text-xs text-gray-500 mb-1">Kompatibel bis (App)</label>
                            <input
                                type="text"
                                value={compatMax}
                                onChange={(e) => setCompatMax(e.target.value)}
                                placeholder=""
                                className="w-full px-3 py-2 rounded-xl border border-black/[0.08] text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400/40"
                            />
                        </div>
                    </div>

                    <div>
                        <label className="block text-xs text-gray-500 mb-1">Config JSON</label>
                        <textarea
                            value={configJson}
                            onChange={(e) => setConfigJson(e.target.value)}
                            rows={10}
                            className="w-full px-3 py-2 rounded-xl border border-black/[0.08] text-sm font-mono focus:outline-none focus:ring-2 focus:ring-emerald-400/40"
                            id="config-json-editor"
                        />
                    </div>

                    <div className="flex items-center gap-3">
                        <button
                            type="submit"
                            disabled={creating || !version}
                            className="px-6 py-2.5 rounded-xl text-sm font-medium bg-emerald-500 text-white hover:bg-emerald-600 disabled:opacity-50 transition-colors shadow-sm"
                        >
                            {creating ? 'Erstelle…' : 'Erstellen'}
                        </button>
                        <button
                            type="button"
                            onClick={() => setShowCreate(false)}
                            className="px-4 py-2.5 rounded-xl text-sm text-gray-500 hover:bg-black/[0.04] transition-colors"
                        >
                            Abbrechen
                        </button>
                    </div>
                </form>
            )}

            {error && (
                <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-xl text-sm">
                    {error}
                </div>
            )}

            {/* Configs List */}
            <div className="space-y-3">
                {loading ? (
                    <div className="glass-card rounded-2xl p-12 text-center text-gray-400">
                        Lade Configs…
                    </div>
                ) : configs.length === 0 ? (
                    <div className="glass-card rounded-2xl p-12 text-center text-gray-400">
                        Keine Config Releases vorhanden
                    </div>
                ) : (
                    configs.map((c) => (
                        <div key={c.id} className="glass-card rounded-2xl overflow-hidden">
                            <div
                                className="flex items-center justify-between px-5 py-4 cursor-pointer hover:bg-black/[0.015] transition-colors"
                                onClick={() => setExpanded(expanded === c.id ? null : c.id)}
                            >
                                <div className="flex items-center gap-4">
                                    <span className="font-mono font-medium text-gray-800">{c.version}</span>
                                    <span className={`px-2 py-0.5 rounded-md text-xs font-medium ${
                                        c.is_active ? 'bg-emerald-50 text-emerald-700' : 'bg-gray-50 text-gray-400'
                                    }`}>
                                        {c.is_active ? '● aktiv' : '○ inaktiv'}
                                    </span>
                                    {c.compatible_app_min && (
                                        <span className="text-xs text-gray-400">
                                            App ≥ {c.compatible_app_min}
                                            {c.compatible_app_max ? ` ≤ ${c.compatible_app_max}` : ''}
                                        </span>
                                    )}
                                </div>
                                <div className="flex items-center gap-3">
                                    <span className="text-xs text-gray-400 font-mono">
                                        SHA256: {c.sha256.substring(0, 8)}…
                                    </span>
                                    <span className="text-xs text-gray-400">
                                        {new Date(c.created_at).toLocaleDateString('de-CH')}
                                    </span>
                                    <button
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            handleToggle(c.id, !c.is_active);
                                        }}
                                        className={`px-3 py-1 rounded-lg text-xs transition-colors ${
                                            c.is_active
                                                ? 'text-amber-600 hover:bg-amber-50'
                                                : 'text-emerald-600 hover:bg-emerald-50'
                                        }`}
                                    >
                                        {c.is_active ? 'Deaktivieren' : 'Aktivieren'}
                                    </button>
                                    <span className="text-gray-300 text-sm">
                                        {expanded === c.id ? '▲' : '▼'}
                                    </span>
                                </div>
                            </div>
                            {expanded === c.id && (
                                <div className="border-t border-black/[0.06] px-5 py-4 bg-black/[0.01]">
                                    <pre className="text-xs font-mono text-gray-600 overflow-x-auto p-3 rounded-xl bg-white border border-black/[0.06]">
                                        {JSON.stringify(c.config_payload, null, 2)}
                                    </pre>
                                </div>
                            )}
                        </div>
                    ))
                )}
            </div>
        </div>
    );
}
