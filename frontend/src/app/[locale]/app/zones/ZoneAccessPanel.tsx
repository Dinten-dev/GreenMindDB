'use client';

import { useEffect, useState } from 'react';
import {
  apiListMemberZones,
  apiUpdateMemberZones,
  type MemberZoneAccess,
  type Zone,
} from '@/lib/api';

export default function ZoneAccessPanel({ zones }: { zones: Zone[] }) {
  const [members, setMembers] = useState<MemberZoneAccess[]>([]);
  const [selected, setSelected] = useState('');
  const [grants, setGrants] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    apiListMemberZones()
      .then((data) => {
        if (active) setMembers(data);
      })
      .catch(() => {
        if (active) setError('Zugänge konnten nicht geladen werden. Bitte erneut öffnen.');
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);
  const member = members.find((item) => item.id === selected);
  const choose = (id: string) => {
    setSelected(id);
    setGrants(members.find((item) => item.id === id)?.zone_ids ?? []);
    setMessage('');
    setError('');
  };
  const save = async () => {
    if (!member || member.all_zones) return;
    setSaving(true);
    setError('');
    setMessage('');
    try {
      const updated = await apiUpdateMemberZones(member.id, grants);
      setMembers((current) => current.map((item) => (item.id === updated.id ? updated : item)));
      setGrants(updated.zone_ids);
      setMessage('Zonenzugänge gespeichert.');
    } catch {
      setError('Zugänge konnten nicht gespeichert werden. Bitte erneut versuchen.');
    } finally {
      setSaving(false);
    }
  };
  return (
    <section aria-labelledby="zone-access-heading" className="glass-card p-6 space-y-4">
      <h2 id="zone-access-heading" className="text-lg font-semibold text-gray-800">
        Zugänge zu Zonen
      </h2>
      <p className="text-sm text-gray-500">
        Mitglieder sehen nur ausgewählte Zonen samt Sensoren, Messungen und Aufnahmen. Eigentümer
        und Administratoren sehen alle Zonen.
      </p>
      {loading ? (
        <p role="status">Zugänge werden geladen …</p>
      ) : (
        <>
          <label className="block text-sm font-medium text-gray-700">
            Person
            <select
              value={selected}
              disabled={saving}
              onChange={(event) => choose(event.target.value)}
              className="mt-2 block w-full rounded-xl border border-gray-200 bg-white p-3"
            >
              <option value="">Person auswählen</option>
              {members.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name || item.email}
                  {item.name ? ` · ${item.email}` : ''}
                </option>
              ))}
            </select>
          </label>
          {member &&
            (member.all_zones ? (
              <p className="text-sm text-emerald-800">
                Diese Person hat durch ihre Rolle Zugriff auf alle Zonen.
              </p>
            ) : (
              <>
                <fieldset disabled={saving} className="space-y-2">
                  <legend className="mb-2 text-sm font-medium text-gray-700">
                    Freigegebene Zonen
                  </legend>
                  {zones.map((zone) => (
                    <label
                      key={zone.id}
                      className="flex items-center gap-3 rounded-xl border border-gray-100 bg-white p-3 text-sm"
                    >
                      <input
                        type="checkbox"
                        className="accent-emerald-600"
                        checked={grants.includes(zone.id)}
                        onChange={(event) => {
                          setMessage('');
                          setGrants((current) =>
                            event.target.checked
                              ? [...current, zone.id]
                              : current.filter((id) => id !== zone.id)
                          );
                        }}
                      />
                      {zone.name}
                    </label>
                  ))}
                </fieldset>
                {grants.length === 0 && (
                  <p className="text-sm text-gray-500">
                    Ohne Auswahl sieht diese Person keine Zonen oder Sensordaten.
                  </p>
                )}
                <button
                  type="button"
                  disabled={saving}
                  onClick={save}
                  className="rounded-xl bg-emerald-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                >
                  {saving ? 'Wird gespeichert …' : 'Zugänge speichern'}
                </button>
              </>
            ))}
        </>
      )}
      {error && (
        <p role="alert" className="text-sm text-red-700">
          {error}
        </p>
      )}
      {message && (
        <p role="status" className="text-sm text-emerald-800">
          {message}
        </p>
      )}
    </section>
  );
}
