'use client';
import { useCallback, useEffect, useState } from 'react';
import {
  listAccessRequests,
  sendInvitation,
  resendInvitation,
  type AccessRequestPage,
  type AccessRequest,
} from '@/lib/invitations-api';

function invitationStatus(item: AccessRequest) {
  const invite = item.invitation;
  if (!invite) return 'Noch nicht eingeladen';
  if (invite.redeemed_at) return 'Eingelöst';
  if (invite.delivery_status === 'failed') return 'Versand fehlgeschlagen';
  if (invite.revoked_at) return 'Widerrufen';
  if (new Date(invite.expires_at).getTime() <= Date.now()) return 'Abgelaufen';
  return invite.delivery_status === 'sent'
    ? 'Vom E-Mail-Dienst zum Versand angenommen'
    : 'Versand noch nicht bestätigt';
}

export default function AccessRequestsPage() {
  const [page, setPage] = useState(1);
  const [data, setData] = useState<AccessRequestPage | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      setData(await listAccessRequests(page));
    } catch {
      setError('Zugangsanfragen konnten nicht geladen werden.');
    }
  }, [page]);
  useEffect(() => {
    void load();
  }, [load]);
  async function invite(item: AccessRequest) {
    setBusy(item.id);
    setError('');
    try {
      if (item.invitation) await resendInvitation(item.invitation.id);
      else await sendInvitation(item.id);
    } catch {
      setError('Versand nicht bestätigt. Status prüfen und bei Bedarf erneut senden.');
    } finally {
      await load();
      setBusy(null);
    }
  }
  return (
    <div className="max-w-5xl space-y-5">
      <h1 className="text-2xl font-bold">Zugangsanfragen</h1>
      {error && (
        <p role="alert" className="text-red-700">
          {error}
        </p>
      )}
      {!data && <p role="status">Anfragen werden geladen …</p>}
      {data?.items.map((item) => (
        <article key={item.id} className="glass-card p-5 space-y-2">
          <h2 className="font-semibold">
            {item.name} · {item.email}
          </h2>
          <p>{[item.company, item.country].filter(Boolean).join(' · ')}</p>
          {item.message && <p className="whitespace-pre-wrap">{item.message}</p>}
          <p role="status">{invitationStatus(item)}</p>
          {item.invitation && (
            <p className="text-sm">
              Gültig bis: {new Date(item.invitation.expires_at).toLocaleString()}
            </p>
          )}
          <button
            type="button"
            className="btn-primary"
            disabled={!!busy || !!item.invitation?.redeemed_at}
            onClick={() => void invite(item)}
          >
            {busy === item.id
              ? 'Versand läuft …'
              : item.invitation
                ? 'Erneut einladen'
                : 'Einladen'}
          </button>
        </article>
      ))}
      {data?.total === 0 && <p>Keine Zugangsanfragen vorhanden.</p>}
      <div className="flex gap-4 items-center">
        <button type="button" disabled={page <= 1 || !!busy} onClick={() => setPage(page - 1)}>
          Zurück
        </button>
        <span>
          Seite {page} · {data?.total ?? 0} Anfragen
        </span>
        <button
          type="button"
          disabled={!data || page * data.page_size >= data.total || !!busy}
          onClick={() => setPage(page + 1)}
        >
          Weiter
        </button>
      </div>
    </div>
  );
}
