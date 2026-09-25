'use client';
import { useEffect, useState, type FormEvent } from 'react';
import Link from 'next/link';
import { useLocale } from 'next-intl';
import {
  adminCatalog,
  adminUpdateCompany,
  adminCreateCompany,
  adminDeleteCompany,
  adminCreateCompanyZone,
  type AdminCatalog,
} from '@/lib/administration-api';

type Company = AdminCatalog['companies'][number];
type Action = { type: 'create' } | { type: 'edit' | 'delete' | 'zone'; company: Company };
export default function Companies() {
  const locale = useLocale();
  const [catalog, setCatalog] = useState<AdminCatalog>({ companies: [], zones: [] });
  const [action, setAction] = useState<Action | null>(null);
  const [name, setName] = useState('');
  const [location, setLocation] = useState('');
  const [zoneType, setZoneType] = useState('GREENHOUSE');
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
        if (active) {
          setCatalog({ companies: [], zones: [] });
          setError('Firmen konnten nicht geladen werden.');
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [reload]);
  const open = (next: Action) => {
    setAction(next);
    setName(next.type === 'edit' ? next.company.name : '');
    setLocation('');
    setZoneType('GREENHOUSE');
    setError('');
    setMessage('');
  };
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!action || saving) return;
    setSaving(true);
    setError('');
    setMessage('');
    try {
      if (action.type === 'create') {
        await adminCreateCompany(name.trim());
        setMessage('Firma erstellt. Du kannst jetzt Zonen anlegen und Benutzer zuordnen.');
      } else if (action.type === 'edit') {
        await adminUpdateCompany(action.company.id, name.trim());
        setMessage('Firma gespeichert. Zonenzugänge bleiben unverändert.');
      } else if (action.type === 'delete') {
        await adminDeleteCompany(action.company.id, name.trim());
        setMessage('Leere Firma gelöscht. Benutzer und Messdaten wurden nicht gelöscht.');
      } else {
        await adminCreateCompanyZone(action.company.id, {
          name: name.trim(),
          location: location.trim() || null,
          zone_type: zoneType,
        });
        setMessage(
          'Firmenzone angelegt. Gib sie den gewünschten Mitgliedern unter Kunden & Benutzer frei.'
        );
      }
      setAction(null);
      setReload((n) => n + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Speichern fehlgeschlagen.');
    } finally {
      setSaving(false);
    }
  };
  const input = 'mt-1 block w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm';
  const button = 'rounded-xl border border-gray-200 px-3 py-2 text-sm disabled:opacity-40';
  return (
    <div className="max-w-5xl space-y-6">
      <Link href={`/${locale}/app/administration`} className="text-sm text-emerald-700">
        ← Administration
      </Link>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold text-gray-800">Firmen verwalten</h1>
        <button
          disabled={saving}
          onClick={() => open({ type: 'create' })}
          className="rounded-xl bg-emerald-700 px-4 py-2 text-sm text-white disabled:opacity-40"
        >
          Firma hinzufügen
        </button>
      </div>
      <p className="text-sm text-gray-600">
        Firmen bündeln ihre Zonen. Mitglieder sehen nur ausdrücklich freigegebene Zonen; Eigentümer
        und Firmenadministratoren sehen alle Zonen ihrer Firma.
      </p>
      <Link
        href={`/${locale}/app/administration/users`}
        className="inline-block text-sm text-emerald-700 underline"
      >
        Benutzer einer Firma zuordnen und Zonen freigeben →
      </Link>
      {error && (
        <p role="alert" className="text-red-700">
          {error}{' '}
          <button disabled={saving} onClick={() => setReload((n) => n + 1)} className="underline">
            Erneut laden
          </button>
        </p>
      )}
      {message && (
        <p role="status" className="text-emerald-700">
          {message}
        </p>
      )}
      {action && (
        <form onSubmit={save} className="glass-card p-6 space-y-4" aria-label="Firma verwalten">
          <h2 className="text-lg font-semibold">
            {action.type === 'create'
              ? 'Neue Firma'
              : action.type === 'edit'
                ? 'Firma bearbeiten'
                : action.type === 'zone'
                  ? `Neue Zone für ${action.company.name}`
                  : `Firma ${action.company.name} löschen`}
          </h2>
          {action.type === 'delete' && (
            <p className="text-sm text-gray-600">
              Nur leere Firmen können gelöscht werden. Zugeordnete Benutzer, Zonen oder andere Daten
              verhindern die Löschung. Es werden keine Sensoren oder Messdaten mitgelöscht.
              Bestätige die endgültige Löschung mit dem vollständigen Firmennamen.
            </p>
          )}
          <fieldset disabled={saving} className="space-y-4">
            <label className="block text-sm">
              {action.type === 'zone'
                ? 'Zonenname'
                : action.type === 'delete'
                  ? 'Firmenname zur Bestätigung'
                  : 'Firmenname'}
              <input
                required
                maxLength={200}
                value={name}
                onChange={(e) => setName(e.target.value)}
                className={input}
              />
            </label>
            {action.type === 'zone' && (
              <>
                <label className="block text-sm">
                  Standort (optional)
                  <input
                    maxLength={500}
                    value={location}
                    onChange={(e) => setLocation(e.target.value)}
                    className={input}
                  />
                </label>
                <label className="block text-sm">
                  Zonentyp
                  <select
                    value={zoneType}
                    onChange={(e) => setZoneType(e.target.value)}
                    className={input}
                  >
                    <option value="GREENHOUSE">Gewächshaus</option>
                    <option value="OPEN_FIELD">Freiland</option>
                    <option value="VERTICAL_FARM">Vertikale Farm</option>
                    <option value="ORCHARD">Obstgarten</option>
                  </select>
                </label>
              </>
            )}
          </fieldset>
          <div className="flex flex-wrap gap-3">
            <button
              disabled={
                saving ||
                !name.trim() ||
                (action.type === 'delete' && name.trim() !== action.company.name)
              }
              className={`rounded-xl px-4 py-2 text-white disabled:opacity-40 ${action.type === 'delete' ? 'bg-red-700' : 'bg-emerald-700'}`}
            >
              {saving
                ? 'Wird gespeichert …'
                : action.type === 'delete'
                  ? 'Endgültig löschen'
                  : action.type === 'zone'
                    ? 'Zone anlegen'
                    : 'Speichern'}
            </button>
            <button
              type="button"
              disabled={saving}
              onClick={() => setAction(null)}
              className={button}
            >
              Abbrechen
            </button>
          </div>
        </form>
      )}
      {loading ? (
        <p role="status">Firmen werden geladen …</p>
      ) : catalog.companies.length === 0 ? (
        <p className="text-sm text-gray-500">Noch keine Firmen vorhanden.</p>
      ) : (
        catalog.companies.map((company) => {
          const zones = catalog.zones.filter((z) => z.organization_id === company.id);
          return (
            <section
              key={company.id}
              className="glass-card p-6 space-y-3"
              aria-label={company.name}
            >
              <h2 className="text-lg font-semibold">{company.name}</h2>
              <p className="text-sm text-gray-600">
                Zugehörige Zonen: {zones.map((z) => z.name).join(' · ') || 'Keine Zonen'}
              </p>
              <div className="flex flex-wrap gap-3">
                <button
                  disabled={saving}
                  onClick={() => open({ type: 'edit', company })}
                  className={button}
                >
                  Firma bearbeiten
                </button>
                <button
                  disabled={saving}
                  onClick={() => open({ type: 'zone', company })}
                  className={button}
                >
                  Zone hinzufügen
                </button>
                <button
                  disabled={saving || zones.length > 0}
                  onClick={() => open({ type: 'delete', company })}
                  className={`${button} text-red-700`}
                >
                  Firma löschen
                </button>
              </div>
              {zones.length > 0 && (
                <p className="text-xs text-gray-500">
                  Löschen gesperrt: Dieser Firma sind Zonen zugeordnet.
                </p>
              )}
            </section>
          );
        })
      )}
    </div>
  );
}
