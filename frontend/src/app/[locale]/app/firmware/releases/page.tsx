'use client';

import { useAppText } from '@/i18n/app-text';

import { useState, useEffect, useCallback } from 'react';
import {
  listReleases,
  uploadFirmware,
  toggleRelease,
  deleteRelease,
  FirmwareRelease,
  FirmwareReleaseList,
} from '@/lib/firmware-api';

export default function ReleasesPage() {
  const tx = useAppText();

  const [data, setData] = useState<FirmwareReleaseList | null>(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [filterActive, setFilterActive] = useState<string>('all');

  // Upload modal
  const [showUpload, setShowUpload] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState('');

  // Confirm dialogs
  const [confirmAction, setConfirmAction] = useState<{
    type: 'toggle' | 'delete';
    release: FirmwareRelease;
  } | null>(null);
  const [actionLoading, setActionLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const isActive = filterActive === 'all' ? undefined : filterActive === 'active';
      const res = await listReleases({ search: search || undefined, is_active: isActive });
      setData(res);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, [search, filterActive]);

  useEffect(() => {
    load();
  }, [load]);

  const handleUpload = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setUploading(true);
    setUploadError('');
    const form = e.currentTarget;
    const fd = new FormData(form);
    try {
      await uploadFirmware(fd);
      setShowUpload(false);
      form.reset();
      load();
    } catch (err: unknown) {
      setUploadError(tx.error(err));
    } finally {
      setUploading(false);
    }
  };

  const handleConfirm = async () => {
    if (!confirmAction) return;
    setActionLoading(true);
    try {
      if (confirmAction.type === 'toggle') {
        await toggleRelease(confirmAction.release.id, !confirmAction.release.is_active);
      } else {
        await deleteRelease(confirmAction.release.id);
      }
      setConfirmAction(null);
      load();
    } catch (err) {
      console.error(err);
    } finally {
      setActionLoading(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center gap-3">
        <input
          type="text"
          placeholder={tx('Version suchen…')}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="px-4 py-2 rounded-xl bg-white/60 border border-black/[0.06] text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-emerald-500/30 backdrop-blur-sm w-60"
        />
        <select
          value={filterActive}
          onChange={(e) => setFilterActive(e.target.value)}
          className="px-3 py-2 rounded-xl bg-white/60 border border-black/[0.06] text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-emerald-500/30 backdrop-blur-sm"
        >
          <option value="all">{tx('Alle')}</option>
          <option value="active">{tx('Aktiv')}</option>
          <option value="inactive">{tx('Inaktiv')}</option>
        </select>
        <div className="flex-1" />
        <button
          onClick={() => {
            setShowUpload(true);
            setUploadError('');
          }}
          className="px-4 py-2 bg-gradient-to-r from-emerald-500 to-emerald-600 text-white rounded-full text-sm font-medium hover:from-emerald-600 hover:to-emerald-700 transition-all shadow-sm"
        >
          {tx('+ Firmware hochladen')}
        </button>
      </div>

      {/* Table */}
      <div className="glass-card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-gray-400 uppercase tracking-wider bg-black/[0.02]">
                <th className="px-4 py-3">{tx('Version')}</th>
                <th className="px-4 py-3">{tx('Board')}</th>
                <th className="px-4 py-3">{tx('HW Rev')}</th>
                <th className="px-4 py-3">{tx('SHA256')}</th>
                <th className="px-4 py-3">{tx('Status')}</th>
                <th className="px-4 py-3">{tx('Pflicht')}</th>
                <th className="px-4 py-3">{tx('Datum')}</th>
                <th className="px-4 py-3 text-right">{tx('Aktionen')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-black/[0.04]">
              {loading ? (
                [...Array(3)].map((_, i) => (
                  <tr key={i}>
                    <td colSpan={8} className="px-4 py-4">
                      <div className="h-4 bg-black/[0.04] rounded w-full animate-pulse" />
                    </td>
                  </tr>
                ))
              ) : data?.items.length === 0 ? (
                <tr>
                  <td colSpan={8} className="px-4 py-12 text-center text-gray-400">
                    {tx('Keine Firmware-Releases gefunden.')}
                  </td>
                </tr>
              ) : (
                data?.items.map((r) => (
                  <tr key={r.id} className="hover:bg-black/[0.01] transition-colors">
                    <td className="px-4 py-3 font-mono font-semibold text-gray-800">{r.version}</td>
                    <td className="px-4 py-3 text-gray-600">{r.board_type}</td>
                    <td className="px-4 py-3 text-gray-600">{r.hardware_revision}</td>
                    <td className="px-4 py-3 font-mono text-xs text-gray-400" title={r.sha256}>
                      {r.sha256.slice(0, 12)}…
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                          r.is_active
                            ? 'bg-emerald-50 text-emerald-600'
                            : 'bg-gray-100 text-gray-400'
                        }`}
                      >
                        {r.is_active ? tx('Aktiv') : tx('Inaktiv')}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      {r.mandatory && (
                        <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-amber-50 text-amber-600">
                          {tx('Pflicht')}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-400">
                      {new Date(r.created_at).toLocaleDateString(tx.locale)}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <div className="flex items-center justify-end gap-1">
                        <button
                          onClick={() => setConfirmAction({ type: 'toggle', release: r })}
                          className={`px-2.5 py-1 rounded-lg text-xs font-medium transition-colors ${
                            r.is_active
                              ? 'text-amber-600 hover:bg-amber-50'
                              : 'text-emerald-600 hover:bg-emerald-50'
                          }`}
                        >
                          {r.is_active ? tx('Deaktivieren') : tx('Aktivieren')}
                        </button>
                        <button
                          onClick={() => setConfirmAction({ type: 'delete', release: r })}
                          className="px-2.5 py-1 rounded-lg text-xs font-medium text-red-500 hover:bg-red-50 transition-colors"
                        >
                          {tx('Löschen')}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        {data && (
          <div className="px-4 py-3 text-xs text-gray-400 border-t border-black/[0.04]">
            {tx('{count, plural, one {# Version insgesamt} other {# Versionen insgesamt}}', {
              count: data.meta.total,
            })}
          </div>
        )}
      </div>

      {/* Upload Modal */}
      {showUpload && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 backdrop-blur-sm">
          <div className="glass-card p-8 w-full max-w-lg mx-4 shadow-xl">
            <h2 className="text-xl font-semibold text-gray-800 mb-6">{tx('Firmware hochladen')}</h2>
            <form onSubmit={handleUpload} className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-medium text-gray-500 mb-1">
                    {tx('Version *')}
                  </label>
                  <input
                    name="version"
                    required
                    placeholder="1.0.0"
                    className="w-full px-3 py-2 rounded-xl bg-white/60 border border-black/[0.06] text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/30"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-500 mb-1">
                    {tx('Board Type *')}
                  </label>
                  <input
                    name="board_type"
                    required
                    placeholder={tx('ESP32_WROOM')}
                    className="w-full px-3 py-2 rounded-xl bg-white/60 border border-black/[0.06] text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/30"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-500 mb-1">
                    {tx('HW Revision *')}
                  </label>
                  <input
                    name="hardware_revision"
                    required
                    placeholder={tx('v1')}
                    className="w-full px-3 py-2 rounded-xl bg-white/60 border border-black/[0.06] text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/30"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-500 mb-1">
                    {tx('Min. Version')}
                  </label>
                  <input
                    name="min_version"
                    placeholder="0.9.0"
                    className="w-full px-3 py-2 rounded-xl bg-white/60 border border-black/[0.06] text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/30"
                  />
                </div>
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">
                  {tx('Changelog')}
                </label>
                <textarea
                  name="changelog"
                  rows={2}
                  placeholder={tx('Was ist neu…')}
                  className="w-full px-3 py-2 rounded-xl bg-white/60 border border-black/[0.06] text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500/30 resize-none"
                />
              </div>
              <div className="flex items-center gap-3">
                <label className="flex items-center gap-2 text-sm text-gray-600 cursor-pointer">
                  <input type="checkbox" name="mandatory" value="true" className="rounded" />
                  {tx('Pflichtupdate')}
                </label>
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">
                  {tx('Firmware-Datei (.bin) *')}
                </label>
                <input
                  type="file"
                  name="file"
                  accept=".bin"
                  required
                  className="w-full text-sm text-gray-600 file:mr-4 file:py-2 file:px-4 file:rounded-xl file:border-0 file:text-sm file:font-medium file:bg-emerald-50 file:text-emerald-600 hover:file:bg-emerald-100 file:cursor-pointer"
                />
              </div>
              {uploadError && (
                <p className="text-sm text-red-600 bg-red-50 rounded-xl px-3 py-2">{uploadError}</p>
              )}
              <div className="flex gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => setShowUpload(false)}
                  className="flex-1 py-2.5 bg-black/[0.04] text-gray-600 rounded-xl text-sm font-medium hover:bg-black/[0.06] transition-colors"
                >
                  {tx('Abbrechen')}
                </button>
                <button
                  type="submit"
                  disabled={uploading}
                  className="flex-1 py-2.5 bg-gradient-to-r from-emerald-500 to-emerald-600 text-white rounded-xl text-sm font-medium hover:from-emerald-600 hover:to-emerald-700 transition-all disabled:opacity-50 shadow-sm"
                >
                  {uploading ? tx('Lade hoch…') : tx('Hochladen')}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Confirm Dialog */}
      {confirmAction && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div
            className="absolute inset-0 bg-black/20 backdrop-blur-sm"
            onClick={() => setConfirmAction(null)}
          />
          <div className="relative bg-white/80 rounded-2xl border border-white/50 shadow-2xl backdrop-blur-xl p-6 max-w-sm w-full">
            <div
              className={`w-12 h-12 rounded-full flex items-center justify-center mb-4 mx-auto ${
                confirmAction.type === 'delete'
                  ? 'bg-red-50 text-red-500'
                  : 'bg-amber-50 text-amber-500'
              }`}
            >
              {confirmAction.type === 'delete' ? '🗑' : '⚙'}
            </div>
            <h3 className="text-lg font-semibold text-center text-gray-800 mb-2">
              {confirmAction.type === 'delete'
                ? tx('Release {value0} löschen?', { value0: confirmAction.release.version })
                : tx('Release {value0} {value1}?', {
                    value0: confirmAction.release.version,
                    value1: confirmAction.release.is_active ? tx('Deaktivieren') : tx('Aktivieren'),
                  })}
            </h3>
            <p className="text-sm text-center text-gray-500 mb-6">
              {confirmAction.type === 'delete'
                ? tx(
                    'Die Firmware-Datei wird permanent gelöscht. Alle verbundenen Policies werden ebenfalls entfernt.'
                  )
                : confirmAction.release.is_active
                  ? tx('Gateways werden diese Version nicht mehr synchronisieren.')
                  : tx('Gateways können diese Version ab sofort synchronisieren.')}
            </p>
            <div className="flex gap-3">
              <button
                onClick={() => setConfirmAction(null)}
                className="flex-1 px-4 py-2.5 bg-white border border-gray-200 text-gray-700 rounded-xl text-sm font-medium hover:bg-gray-50 transition-colors"
              >
                {tx('Abbrechen')}
              </button>
              <button
                onClick={handleConfirm}
                disabled={actionLoading}
                className={`flex-1 px-4 py-2.5 text-white rounded-xl text-sm font-medium transition-colors disabled:opacity-50 ${
                  confirmAction.type === 'delete'
                    ? 'bg-red-500 hover:bg-red-600'
                    : 'bg-emerald-500 hover:bg-emerald-600'
                }`}
              >
                {actionLoading ? tx('Bitte warten…') : tx('Bestätigen')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
