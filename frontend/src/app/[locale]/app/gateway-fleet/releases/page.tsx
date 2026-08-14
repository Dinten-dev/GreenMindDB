'use client';

import { useEffect, useState, useCallback } from 'react';
import {
    listAppReleases,
    uploadAppRelease,
    toggleAppRelease,
    deleteAppRelease,
    startRollout,
    type GatewayAppRelease,
} from '@/lib/gateway-admin-api';

export default function GatewayReleasesPage() {
    const [releases, setReleases] = useState<GatewayAppRelease[]>([]);
    const [total, setTotal] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [showUpload, setShowUpload] = useState(false);
    const [uploading, setUploading] = useState(false);

    // Upload form state
    const [file, setFile] = useState<File | null>(null);
    const [version, setVersion] = useState('');
    const [channel, setChannel] = useState('stable');
    const [mandatory, setMandatory] = useState(false);
    const [changelog, setChangelog] = useState('');
    const [signature, setSignature] = useState('');

    const loadReleases = useCallback(async () => {
        try {
            setLoading(true);
            const data = await listAppReleases({ limit: 50 });
            setReleases(data.items);
            setTotal(data.total);
            setError(null);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to load releases');
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { loadReleases(); }, [loadReleases]);

    const handleUpload = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!file || !version) return;

        setUploading(true);
        try {
            const formData = new FormData();
            formData.append('file', file);
            formData.append('version', version);
            formData.append('channel', channel);
            formData.append('mandatory', String(mandatory));
            if (changelog) formData.append('changelog', changelog);
            if (signature) formData.append('signature', signature);

            await uploadAppRelease(formData);
            setShowUpload(false);
            setFile(null);
            setVersion('');
            setChangelog('');
            setSignature('');
            loadReleases();
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Upload failed');
        } finally {
            setUploading(false);
        }
    };

    const handleToggle = async (id: string, active: boolean) => {
        try {
            await toggleAppRelease(id, active);
            loadReleases();
        } catch (err) {
            alert(err instanceof Error ? err.message : 'Toggle failed');
        }
    };

    const handleDelete = async (id: string, ver: string) => {
        if (!confirm(`Release ${ver} wirklich löschen?`)) return;
        try {
            await deleteAppRelease(id);
            loadReleases();
        } catch (err) {
            alert(err instanceof Error ? err.message : 'Delete failed');
        }
    };

    const handleRollout = async (version: string, ring: string) => {
        try {
            const result = await startRollout({ release_version: version, target_ring: ring });
            alert(`Rollout gestartet: ${result.gateways_updated} Gateways aktualisiert`);
        } catch (err) {
            alert(err instanceof Error ? err.message : 'Rollout failed');
        }
    };

    const formatSize = (bytes: number | null) => {
        if (!bytes) return '—';
        if (bytes < 1024) return `${bytes} B`;
        if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
        return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    };

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h2 className="text-lg font-semibold text-gray-800">Gateway App Releases</h2>
                    <p className="text-xs text-gray-400 mt-0.5">{total} Releases</p>
                </div>
                <button
                    onClick={() => setShowUpload(!showUpload)}
                    className="px-4 py-2 rounded-xl text-sm font-medium bg-emerald-500 text-white hover:bg-emerald-600 transition-colors shadow-sm"
                    id="upload-release-btn"
                >
                    + Release hochladen
                </button>
            </div>

            {/* Upload Form */}
            {showUpload && (
                <form onSubmit={handleUpload} className="glass-card rounded-2xl p-6 space-y-4">
                    <h3 className="font-semibold text-gray-800">Neues Release hochladen</h3>
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                        <div>
                            <label className="block text-xs text-gray-500 mb-1">Version (semver)</label>
                            <input
                                type="text"
                                value={version}
                                onChange={(e) => setVersion(e.target.value)}
                                placeholder="1.2.0"
                                className="w-full px-3 py-2 rounded-xl border border-black/[0.08] text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400/40"
                                required
                                id="release-version-input"
                            />
                        </div>
                        <div>
                            <label className="block text-xs text-gray-500 mb-1">Channel</label>
                            <select
                                value={channel}
                                onChange={(e) => setChannel(e.target.value)}
                                className="w-full px-3 py-2 rounded-xl border border-black/[0.08] text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400/40"
                                id="release-channel-select"
                            >
                                <option value="stable">stable</option>
                                <option value="beta">beta</option>
                                <option value="canary">canary</option>
                            </select>
                        </div>
                        <div className="flex items-end">
                            <label className="flex items-center gap-2 text-sm text-gray-600">
                                <input
                                    type="checkbox"
                                    checked={mandatory}
                                    onChange={(e) => setMandatory(e.target.checked)}
                                    className="rounded"
                                />
                                Mandatory Update
                            </label>
                        </div>
                    </div>

                    <div>
                        <label className="block text-xs text-gray-500 mb-1">Tarball (.tar.gz)</label>
                        <input
                            type="file"
                            accept=".tar.gz,.tgz"
                            onChange={(e) => setFile(e.target.files?.[0] || null)}
                            className="w-full text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded-xl file:border-0 file:text-sm file:font-medium file:bg-emerald-50 file:text-emerald-700 hover:file:bg-emerald-100 transition-colors"
                            required
                            id="release-file-input"
                        />
                    </div>

                    <div>
                        <label className="block text-xs text-gray-500 mb-1">Ed25519 Signatur (Base64, optional)</label>
                        <input
                            type="text"
                            value={signature}
                            onChange={(e) => setSignature(e.target.value)}
                            placeholder="Base64-encoded Ed25519 signature"
                            className="w-full px-3 py-2 rounded-xl border border-black/[0.08] text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400/40 font-mono"
                        />
                    </div>

                    <div>
                        <label className="block text-xs text-gray-500 mb-1">Changelog (Markdown)</label>
                        <textarea
                            value={changelog}
                            onChange={(e) => setChangelog(e.target.value)}
                            rows={3}
                            className="w-full px-3 py-2 rounded-xl border border-black/[0.08] text-sm focus:outline-none focus:ring-2 focus:ring-emerald-400/40"
                            placeholder="Was ist neu in dieser Version?"
                            id="release-changelog-input"
                        />
                    </div>

                    <div className="flex items-center gap-3 pt-2">
                        <button
                            type="submit"
                            disabled={uploading || !file || !version}
                            className="px-6 py-2.5 rounded-xl text-sm font-medium bg-emerald-500 text-white hover:bg-emerald-600 disabled:opacity-50 transition-colors shadow-sm"
                        >
                            {uploading ? 'Uploading…' : 'Upload'}
                        </button>
                        <button
                            type="button"
                            onClick={() => setShowUpload(false)}
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

            {/* Releases Table */}
            <div className="glass-card rounded-2xl overflow-hidden">
                <div className="overflow-x-auto">
                    <table className="w-full text-sm" id="releases-table">
                        <thead>
                            <tr className="border-b border-black/[0.06]">
                                {['Version', 'Channel', 'Status', 'SHA256', 'Signatur', 'Größe', 'Erstellt', 'Aktionen'].map((h) => (
                                    <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-gray-400 uppercase tracking-wider">
                                        {h}
                                    </th>
                                ))}
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-black/[0.04]">
                            {loading ? (
                                <tr>
                                    <td colSpan={8} className="px-4 py-12 text-center text-gray-400">
                                        Lade Releases…
                                    </td>
                                </tr>
                            ) : releases.length === 0 ? (
                                <tr>
                                    <td colSpan={8} className="px-4 py-12 text-center text-gray-400">
                                        Keine Releases vorhanden
                                    </td>
                                </tr>
                            ) : (
                                releases.map((r) => (
                                    <tr key={r.id} className="hover:bg-black/[0.015] transition-colors">
                                        <td className="px-4 py-3">
                                            <span className="font-mono font-medium text-gray-800">{r.version}</span>
                                            {r.mandatory && (
                                                <span className="ml-2 px-1.5 py-0.5 rounded text-[10px] font-bold bg-red-50 text-red-600 uppercase">
                                                    mandatory
                                                </span>
                                            )}
                                        </td>
                                        <td className="px-4 py-3">
                                            <span className="px-2 py-0.5 rounded-md text-xs font-medium bg-gray-50 text-gray-600">
                                                {r.channel}
                                            </span>
                                        </td>
                                        <td className="px-4 py-3">
                                            <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-medium ${
                                                r.is_active ? 'bg-emerald-50 text-emerald-700' : 'bg-gray-50 text-gray-400'
                                            }`}>
                                                {r.is_active ? '● aktiv' : '○ inaktiv'}
                                            </span>
                                        </td>
                                        <td className="px-4 py-3">
                                            <span className="font-mono text-xs text-gray-400">
                                                {r.sha256.substring(0, 12)}…
                                            </span>
                                        </td>
                                        <td className="px-4 py-3">
                                            {r.signature ? (
                                                <span className="text-xs text-emerald-600 font-medium">✓ signed</span>
                                            ) : (
                                                <span className="text-xs text-amber-500">⚠ unsigned</span>
                                            )}
                                        </td>
                                        <td className="px-4 py-3 text-xs text-gray-500">
                                            {formatSize(r.file_size_bytes)}
                                        </td>
                                        <td className="px-4 py-3 text-xs text-gray-400">
                                            {new Date(r.created_at).toLocaleDateString('de-CH')}
                                        </td>
                                        <td className="px-4 py-3">
                                            <div className="flex items-center gap-1.5">
                                                <button
                                                    onClick={() => handleToggle(r.id, !r.is_active)}
                                                    className={`px-2 py-1 rounded-lg text-xs transition-colors ${
                                                        r.is_active
                                                            ? 'text-amber-600 hover:bg-amber-50'
                                                            : 'text-emerald-600 hover:bg-emerald-50'
                                                    }`}
                                                >
                                                    {r.is_active ? 'Deaktivieren' : 'Aktivieren'}
                                                </button>
                                                {r.is_active && (
                                                    <button
                                                        onClick={() => handleRollout(r.version, 'canary')}
                                                        className="px-2 py-1 rounded-lg text-xs text-blue-600 hover:bg-blue-50 transition-colors"
                                                    >
                                                        Rollout →
                                                    </button>
                                                )}
                                                <button
                                                    onClick={() => handleDelete(r.id, r.version)}
                                                    className="px-2 py-1 rounded-lg text-xs text-red-400 hover:bg-red-50 hover:text-red-600 transition-colors"
                                                >
                                                    ✕
                                                </button>
                                            </div>
                                        </td>
                                    </tr>
                                ))
                            )}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    );
}
