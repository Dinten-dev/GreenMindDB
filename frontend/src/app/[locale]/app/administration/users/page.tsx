'use client';
import { useCallback, useEffect, useState, type FormEvent } from 'react';
import Link from 'next/link';
import { useLocale } from 'next-intl';
import {
  adminCatalog,
  adminUsers,
  adminCreateUser,
  adminUpdateUser,
  adminDeleteUser,
  type AdminCatalog,
  type ManagedUser,
} from '@/lib/administration-api';
const empty = {
  name: '',
  email: '',
  password: '',
  phone_number: '',
  organization_id: '',
  role: 'member' as ManagedUser['role'],
  is_active: true,
  zone_ids: [] as string[],
};
export default function UserManagement() {
  const locale = useLocale();
  const [catalog, setCatalog] = useState<AdminCatalog>({ companies: [], zones: [] });
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [editing, setEditing] = useState<ManagedUser | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState(empty);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [reload, setReload] = useState(0);
  const [deleting, setDeleting] = useState<ManagedUser | null>(null);
  const [confirmation, setConfirmation] = useState('');
  const remove = async (event: FormEvent) => {
    event.preventDefault();
    if (!deleting) return;
    setSaving(true);
    setError('');
    setMessage('');
    try {
      await adminDeleteUser(deleting.id, confirmation.trim());
      setDeleting(null);
      setConfirmation('');
      setShowForm(false);
      setForm(empty);
      setMessage('Benutzerkonto gelöscht. Messdaten und Firmen bleiben erhalten.');
      setOffset(0);
      setReload((n) => n + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Löschen fehlgeschlagen.');
    } finally {
      setSaving(false);
    }
  };
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError('');
    Promise.all([adminCatalog(), adminUsers(query, offset)])
      .then(([c, r]) => {
        if (active) {
          setCatalog(c);
          setUsers(r.users);
          setTotal(r.total);
        }
      })
      .catch(() => {
        if (active) {
          setError('Benutzer konnten nicht geladen werden.');
          setUsers([]);
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [query, offset, reload]);
  const edit = useCallback((user: ManagedUser | null) => {
    setDeleting(null);
    setConfirmation('');
    setEditing(user);
    setForm(
      user
        ? {
            ...empty,
            ...user,
            name: user.name ?? '',
            organization_id: user.organization_id ?? '',
            phone_number: user.phone_number ?? '',
            zone_ids: user.zone_ids,
          }
        : empty
    );
    setShowForm(true);
    setMessage('');
    setError('');
  }, []);
  const save = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError('');
    setMessage('');
    try {
      const assignment = {
        name: form.name,
        organization_id: form.organization_id,
        role: form.role,
        zone_ids: form.role === 'member' ? form.zone_ids : [],
      };
      if (editing)
        await adminUpdateUser(editing.id, {
          ...assignment,
          is_active: form.is_active,
          phone_number: form.phone_number || null,
        });
      else await adminCreateUser({ ...assignment, email: form.email, password: form.password });
      setForm(empty);
      setShowForm(false);
      setEditing(null);
      setMessage('Benutzer gespeichert.');
      setReload((n) => n + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Speichern fehlgeschlagen.');
    } finally {
      setSaving(false);
    }
  };
  const input = 'mt-1 w-full rounded-xl border border-gray-200 bg-white px-3 py-2 text-sm';
  return (
    <div className="max-w-6xl space-y-6">
      <Link href={`/${locale}/app/administration`} className="text-sm text-emerald-700">
        ← Administration
      </Link>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold text-gray-800">Kunden &amp; Benutzer</h1>
        <button
          type="button"
          disabled={saving}
          onClick={() => edit(null)}
          className="rounded-xl bg-emerald-700 px-4 py-2 text-sm text-white"
        >
          Benutzer erstellen
        </button>
      </div>
      {error && (
        <p role="alert" className="text-sm text-red-700">
          {error}{' '}
          <button onClick={() => setReload((n) => n + 1)} className="underline">
            Erneut laden
          </button>
        </p>
      )}
      {message && (
        <p role="status" className="text-sm text-emerald-700">
          {message}
        </p>
      )}
      <p className="text-sm text-gray-600">
        Kunden bearbeiten und genau prüfen, welche Zonen sie sehen.{' '}
        <Link
          href={`/${locale}/app/administration/companies`}
          className="text-emerald-700 underline"
        >
          Firmen bearbeiten
        </Link>
      </p>
      {deleting && (
        <form
          onSubmit={remove}
          className="glass-card border border-red-200 p-6 space-y-4"
          aria-label="Kontolöschung bestätigen"
        >
          <h2 className="text-lg font-semibold">Benutzerkonto dauerhaft löschen</h2>
          <p className="text-sm">
            {deleting.name || deleting.email} ({deleting.email}) verliert den Zugang. Das Konto und
            seine Zonenfreigaben werden gelöscht. Firmen, Sensoren und Messdaten bleiben erhalten.
            Dieser Vorgang kann nicht rückgängig gemacht werden.
          </p>
          <label className="block text-sm">
            E-Mail zur Bestätigung
            <input
              type="email"
              required
              autoComplete="off"
              disabled={saving}
              className={input}
              value={confirmation}
              onChange={(e) => setConfirmation(e.target.value)}
            />
          </label>
          <div className="flex gap-3">
            <button
              disabled={
                saving || confirmation.trim().toLowerCase() !== deleting.email.toLowerCase()
              }
              className="rounded-xl bg-red-700 px-4 py-2 text-sm text-white disabled:opacity-40"
            >
              Endgültig löschen
            </button>
            <button
              type="button"
              disabled={saving}
              onClick={() => {
                setDeleting(null);
                setConfirmation('');
              }}
              className="rounded-xl border px-4 py-2 text-sm"
            >
              Abbrechen
            </button>
          </div>
        </form>
      )}
      {showForm && (
        <form onSubmit={save} className="glass-card p-6 space-y-4">
          <h2 className="text-lg font-semibold">
            {editing ? 'Benutzer bearbeiten' : 'Neuer Benutzer'}
          </h2>
          <fieldset disabled={saving} className="grid gap-4 sm:grid-cols-2">
            <label className="text-sm">
              Name
              <input
                required
                maxLength={200}
                className={input}
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </label>
            <label className="text-sm">
              E-Mail
              <input
                type="email"
                required
                maxLength={255}
                disabled={!!editing}
                className={input}
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
              />
            </label>
            {!editing && (
              <label className="text-sm">
                Startpasswort
                <input
                  type="password"
                  required
                  minLength={12}
                  maxLength={128}
                  autoComplete="new-password"
                  className={input}
                  value={form.password}
                  onChange={(e) => setForm({ ...form, password: e.target.value })}
                />
                <span className="mt-1 block text-xs text-gray-500">
                  Mindestens 12 Zeichen, Gross-/Kleinbuchstaben und eine Zahl.
                </span>
              </label>
            )}
            {editing && (
              <label className="text-sm">
                Telefon
                <input
                  maxLength={50}
                  className={input}
                  value={form.phone_number}
                  onChange={(e) => setForm({ ...form, phone_number: e.target.value })}
                />
              </label>
            )}
            <label className="text-sm">
              Firma
              <select
                required
                className={input}
                value={form.organization_id}
                onChange={(e) =>
                  setForm({ ...form, organization_id: e.target.value, zone_ids: [] })
                }
              >
                <option value="">Firma auswählen</option>
                {catalog.companies.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-sm">
              Rolle
              <select
                className={input}
                value={form.role}
                onChange={(e) =>
                  setForm({ ...form, role: e.target.value as ManagedUser['role'], zone_ids: [] })
                }
              >
                <option value="member">Mitglied – ausgewählte Zonen</option>
                <option value="owner">Eigentümer – alle Firmenzonen</option>
                <option value="admin">Administrator – bestehende Admin-Werkzeuge</option>
              </select>
            </label>
            {editing && (
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={form.is_active}
                  onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
                />
                Konto aktiv
              </label>
            )}
            <fieldset className="space-y-2 sm:col-span-2">
              <legend className="mb-2 text-sm font-medium">Zonenzugänge</legend>
              {form.role !== 'member' ? (
                <p className="text-sm text-gray-500">
                  Diese Rolle hat Zugriff auf alle Zonen der Firma. Die zentrale Administration wird
                  separat freigegeben.
                </p>
              ) : (
                <>
                  {catalog.zones
                    .filter((z) => z.organization_id === form.organization_id)
                    .map((z) => (
                      <label
                        key={z.id}
                        className="flex items-center gap-3 rounded-xl border border-gray-100 p-3 text-sm"
                      >
                        <input
                          type="checkbox"
                          className="accent-emerald-600"
                          checked={form.zone_ids.includes(z.id)}
                          onChange={(e) =>
                            setForm({
                              ...form,
                              zone_ids: e.target.checked
                                ? [...form.zone_ids, z.id]
                                : form.zone_ids.filter((id) => id !== z.id),
                            })
                          }
                        />
                        {z.name}
                      </label>
                    ))}
                  <p className="text-xs text-gray-500">
                    Ohne Auswahl sind keine Zonen sichtbar. Ein Firmenwechsel entfernt bisherige
                    Zonenzugänge.
                  </p>
                </>
              )}
            </fieldset>
          </fieldset>
          {!editing && (
            <p className="text-xs text-gray-500">
              Das administrativ angelegte Konto kann sich sofort anmelden. Es wird keine E-Mail
              verschickt. Übermittle die Zugangsdaten persönlich und sicher.
            </p>
          )}
          <div className="flex gap-3">
            <button
              disabled={saving}
              className="rounded-xl bg-emerald-700 px-4 py-2 text-sm text-white disabled:opacity-50"
            >
              {saving ? 'Wird gespeichert …' : 'Speichern'}
            </button>
            <button
              type="button"
              disabled={saving}
              onClick={() => {
                setShowForm(false);
                setForm(empty);
              }}
              className="rounded-xl border border-gray-200 px-4 py-2 text-sm"
            >
              Abbrechen
            </button>
          </div>
        </form>
      )}
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          setQuery(search);
          setOffset(0);
        }}
      >
        <label className="flex-1 text-sm">
          Benutzer suchen
          <input
            className={input}
            maxLength={200}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Name oder E-Mail"
          />
        </label>
        <button className="self-end rounded-xl border border-gray-200 bg-white px-4 py-2 text-sm">
          Suchen
        </button>
      </form>
      <section aria-label="Benutzer" className="glass-card divide-y divide-gray-100">
        {loading ? (
          <p role="status" className="p-6">
            Benutzer werden geladen …
          </p>
        ) : users.length === 0 ? (
          <p className="p-6 text-sm text-gray-500">Keine Benutzer gefunden.</p>
        ) : (
          users.map((u) => (
            <article key={u.id} className="flex flex-wrap items-center justify-between gap-3 p-5">
              <div className="min-w-0">
                <h2 className="break-words font-medium text-gray-800">{u.name || u.email}</h2>
                <p className="break-all text-sm text-gray-500">{u.email}</p>
                <p className="mt-1 text-xs text-gray-500">
                  {u.organization_name || 'Keine Firma'} ·{' '}
                  {{ member: 'Mitglied', owner: 'Eigentümer', admin: 'Administrator' }[u.role]} ·{' '}
                  {u.is_active ? 'Aktiv' : 'Deaktiviert'}
                  {!u.is_verified ? ' · E-Mail unbestätigt' : ''}
                </p>
                <p className="mt-3 text-sm font-medium text-gray-700">Sichtbare Zonen</p>
                <p className="mt-1 text-sm text-gray-600">
                  {u.visible_zones?.length
                    ? u.visible_zones.map((z) => z.name).join(' · ')
                    : 'Keine Zonen sichtbar'}
                </p>
                <p className="mt-1 text-xs text-gray-500">{u.access_note}</p>
              </div>
              {u.protected ? (
                <span className="text-xs text-gray-500">Geschütztes Administrationskonto</span>
              ) : (
                <div className="flex gap-2">
                  <button
                    type="button"
                    disabled={saving}
                    onClick={() => edit(u)}
                    className="rounded-xl border border-gray-200 px-3 py-2 text-sm"
                  >
                    Bearbeiten
                  </button>
                  <button
                    type="button"
                    disabled={saving}
                    onClick={() => {
                      setDeleting(u);
                      setConfirmation('');
                      setShowForm(false);
                      setForm(empty);
                      setError('');
                      setMessage('');
                    }}
                    className="rounded-xl border border-red-200 px-3 py-2 text-sm text-red-700"
                  >
                    Konto löschen
                  </button>
                </div>
              )}
            </article>
          ))
        )}
      </section>
      <div className="flex items-center justify-between gap-3 text-sm">
        <button
          disabled={offset === 0 || loading}
          onClick={() => setOffset(Math.max(0, offset - 50))}
          className="disabled:opacity-40"
        >
          ← Zurück
        </button>
        <span>{total} Benutzer</span>
        <button
          disabled={offset + 50 >= total || loading}
          onClick={() => setOffset(offset + 50)}
          className="disabled:opacity-40"
        >
          Weiter →
        </button>
      </div>
    </div>
  );
}
