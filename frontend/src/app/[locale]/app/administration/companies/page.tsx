'use client';
import { useEffect, useState, type FormEvent } from 'react';
import Link from 'next/link';
import { useLocale } from 'next-intl';
import { adminCatalog, adminUpdateCompany, type AdminCatalog } from '@/lib/administration-api';
export default function Companies() {
  const locale = useLocale();
  const [catalog, setCatalog] = useState<AdminCatalog>({ companies: [], zones: [] });
  const [editing, setEditing] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [reload, setReload] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError('');
    adminCatalog()
      .then((data) => {
        if (active) setCatalog(data);
      })
      .catch(() => {
        if (active) setError('Firmen konnten nicht geladen werden.');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [reload]);
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!editing) return;
    setSaving(true);
    setError('');
    setMessage('');
    try {
      await adminUpdateCompany(editing, name.trim());
      setEditing(null);
      setMessage('Firma gespeichert. Zonenzugänge bleiben unverändert.');
      setReload((n) => n + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Speichern fehlgeschlagen.');
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="max-w-5xl space-y-6">
      <Link href={`/${locale}/app/administration`} className="text-sm text-emerald-700">
        ← Administration
      </Link>
      <h1 className="text-2xl font-bold text-gray-800">Firmen verwalten</h1>
      <p className="text-sm text-gray-600">
        Eine Firmenzone ist für Eigentümer und Administratoren sichtbar. Mitglieder benötigen eine
        eigene Zonenfreigabe unter Kunden &amp; Benutzer.
      </p>
      {error && (
        <p role="alert" className="text-red-700">
          {error}{' '}
          <button onClick={() => setReload((n) => n + 1)} className="underline">
            Erneut laden
          </button>
        </p>
      )}
      {message && (
        <p role="status" className="text-emerald-700">
          {message}
        </p>
      )}
      {loading ? (
        <p role="status">Firmen werden geladen …</p>
      ) : (
        catalog.companies.map((company) => (
          <section key={company.id} className="glass-card p-6 space-y-3">
            <h2 className="text-lg font-semibold">{company.name}</h2>
            <p className="text-sm text-gray-600">
              Zugehörige Zonen:{' '}
              {catalog.zones
                .filter((z) => z.organization_id === company.id)
                .map((z) => z.name)
                .join(' · ') || 'Keine Zonen'}
            </p>
            {editing === company.id ? (
              <form onSubmit={save} className="space-y-3">
                <label className="block text-sm">
                  Firmenname
                  <input
                    required
                    maxLength={200}
                    value={name}
                    disabled={saving}
                    onChange={(e) => setName(e.target.value)}
                    className="mt-1 block w-full rounded-xl border border-gray-200 px-3 py-2"
                  />
                </label>
                <div className="flex gap-3">
                  <button
                    disabled={saving || !name.trim()}
                    className="rounded-xl bg-emerald-700 px-4 py-2 text-white disabled:opacity-40"
                  >
                    Speichern
                  </button>
                  <button type="button" disabled={saving} onClick={() => setEditing(null)}>
                    Abbrechen
                  </button>
                </div>
              </form>
            ) : (
              <button
                disabled={saving}
                onClick={() => {
                  setEditing(company.id);
                  setName(company.name);
                  setMessage('');
                }}
                className="rounded-xl border border-gray-200 px-3 py-2 text-sm"
              >
                Firma bearbeiten
              </button>
            )}
          </section>
        ))
      )}
    </div>
  );
}
