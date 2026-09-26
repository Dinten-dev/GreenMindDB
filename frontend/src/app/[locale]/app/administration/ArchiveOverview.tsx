'use client';
import { useEffect, useState } from 'react';
import {
  adminArchiveStatus,
  adminRequestArchiveCopy,
  type ArchiveOverview as ArchiveOverviewData,
} from '@/lib/administration-api';

const formatBytes = (value: number | null, decimals = 1) =>
  value === null
    ? '–'
    : `${(value / 1024 ** 3).toLocaleString('de-CH', { maximumFractionDigits: decimals })} GB`;

const formatRate = (value: number | null) =>
  value === null
    ? '–'
    : `${(value / 1024 ** 2).toLocaleString('de-CH', { maximumFractionDigits: 2 })} MB/s`;

function ArchiveIcon({ kind }: { kind: 'files' | 'pending' | 'speed' | 'memory' | 'box' }) {
  const paths = {
    files: (
      <>
        <path d="M6 3h8l4 4v14H6z" />
        <path d="M14 3v5h5M9 13h6M9 17h6" />
      </>
    ),
    pending: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v5l3 2" />
      </>
    ),
    speed: (
      <>
        <path d="M4 17a8 8 0 1 1 16 0" />
        <path d="m12 13 4-4M6 17h12" />
      </>
    ),
    memory: (
      <>
        <rect x="5" y="5" width="14" height="14" rx="2" />
        <path d="M9 9h6v6H9zM9 1v4m6-4v4M9 19v4m6-4v4M1 9h4m14 0h4M1 15h4m14 0h4" />
      </>
    ),
    box: (
      <>
        <path d="m3 7 9-4 9 4-9 4zM3 7v10l9 4 9-4V7M12 11v10" />
        <path d="m7.5 5 9 4" />
      </>
    ),
  };
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="h-5 w-5"
    >
      {paths[kind]}
    </svg>
  );
}

function MetricCard({
  icon,
  title,
  value,
  detail,
  tone,
}: {
  icon: 'files' | 'pending' | 'speed' | 'memory';
  title: string;
  value: string;
  detail: string;
  tone: 'green' | 'amber' | 'slate';
}) {
  const colors = {
    green: 'bg-emerald-50 text-emerald-700',
    amber: 'bg-amber-50 text-amber-700',
    slate: 'bg-slate-100 text-slate-600',
  };
  return (
    <article className="rounded-2xl border border-gray-100 bg-white/70 p-5 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm font-medium text-gray-600">{title}</p>
        <span className={`rounded-xl p-2 ${colors[tone]}`}>
          <ArchiveIcon kind={icon} />
        </span>
      </div>
      <p className="mt-4 text-2xl font-semibold tracking-tight text-gray-800">{value}</p>
      <p className="mt-1 text-xs text-gray-500">{detail}</p>
      <div
        aria-hidden="true"
        className={`mt-4 h-1 rounded-full ${tone === 'green' ? 'bg-emerald-200' : tone === 'amber' ? 'bg-amber-200' : 'bg-slate-200'}`}
      />
    </article>
  );
}

export default function ArchiveOverview() {
  const [data, setData] = useState<ArchiveOverviewData | null>(null);
  const [error, setError] = useState(false);
  const [requesting, setRequesting] = useState(false);
  const [notice, setNotice] = useState('');

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const next = await adminArchiveStatus();
        if (active) {
          setData(next);
          setError(false);
        }
      } catch {
        if (active) {
          setData(null);
          setError(true);
        }
      } finally {
        if (active) timer = setTimeout(refresh, 5000);
      }
    };
    void refresh();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, []);

  const requestCopy = async () => {
    if (!data?.manual_copy_available || requesting) return;
    if (!window.confirm('WAV-Dateien zur Storage Box kopieren? Lokale Originale bleiben erhalten.')) {
      return;
    }
    setRequesting(true);
    setNotice('');
    try {
      await adminRequestArchiveCopy();
      setNotice('Kopierauftrag wurde an den Dienst übergeben.');
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : 'Kopierauftrag fehlgeschlagen.');
    } finally {
      setRequesting(false);
    }
  };

  const unavailable = !data || !data.configured;
  const ram =
    data?.worker_memory_bytes === null || data?.worker_memory_bytes === undefined
      ? '–'
      : `${formatBytes(data.worker_memory_bytes)} / ${formatBytes(data.worker_memory_limit_bytes)}`;
  const reserve =
    data?.worker_memory_reserve_bytes == null
      ? 'Keine Live-Messung verfügbar'
      : `${formatBytes(data.worker_memory_reserve_bytes)} Sicherheitsreserve`;
  const stampedAt = data?.sampled_at
    ? `Stand ${new Date(data.sampled_at).toLocaleTimeString('de-CH')}`
    : 'Wartet auf Verbindung zum Kopierdienst';

  return (
    <section className="glass-card space-y-5 p-5 sm:p-6" aria-labelledby="archive-overview-title">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="archive-overview-title" className="text-lg font-semibold text-gray-800">
            WAV-Archiv
          </h2>
          <p className="mt-1 text-sm text-gray-500">
            Kopierstatus, Arbeitsspeicher und Storage Box.
          </p>
        </div>
        <span className="inline-flex items-center gap-2 rounded-full bg-slate-100 px-3 py-1.5 text-xs font-medium text-slate-700">
          <span className="h-2 w-2 rounded-full bg-slate-400" />
          {data?.state === 'running'
            ? 'Aktiver Transfer'
            : data?.state === 'paused'
              ? 'Pausiert'
              : data?.state === 'failed'
                ? 'Fehler'
                : data?.state === 'idle'
                  ? 'Bereit'
                  : 'Nicht verbunden'}
        </span>
      </div>

      {error ? (
        <p
          role="status"
          className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900"
        >
          Archivstatus nicht erreichbar. Es werden keine Werte erfunden oder als 0 bewertet.
        </p>
      ) : unavailable ? (
        <p
          role="status"
          className="rounded-xl border border-emerald-100 bg-emerald-50/70 p-4 text-sm text-emerald-900"
        >
          {data?.message ?? 'Archivstatus wird geladen …'}
        </p>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          icon="files"
          title="Verifiziert kopiert"
          value={data?.files_copied == null ? '–' : data.files_copied.toLocaleString('de-CH')}
          detail="Nur vollständig geprüfte WAV-Dateien"
          tone="green"
        />
        <MetricCard
          icon="pending"
          title="Noch ausstehend"
          value={formatBytes(data?.pending_bytes ?? null)}
          detail="Katalogisierte Dateien, Schätzwert"
          tone="amber"
        />
        <MetricCard
          icon="speed"
          title="Übertragung"
          value={formatRate(data?.transfer_bytes_per_second ?? null)}
          detail={stampedAt}
          tone="slate"
        />
        <MetricCard
          icon="memory"
          title="RAM Kopierdienst"
          value={ram}
          detail={reserve}
          tone="slate"
        />
      </div>

      <div className="rounded-2xl border border-gray-100 bg-white/70 p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-center gap-3">
            <span className="rounded-xl bg-emerald-50 p-2.5 text-emerald-700">
              <ArchiveIcon kind="box" />
            </span>
            <div>
              <h3 className="font-semibold text-gray-800">Storage Box</h3>
              <p className="mt-0.5 text-sm text-gray-500">Belegung und manueller Kopierstart</p>
            </div>
          </div>
          <p className="text-sm font-medium text-gray-600">
            {data?.storage_box_used_bytes != null && data.storage_box_total_bytes != null
              ? `${formatBytes(data.storage_box_used_bytes)} von ${formatBytes(data.storage_box_total_bytes)} belegt`
              : 'Belegung nicht verfügbar'}
          </p>
        </div>
        <div
          className="mt-4 h-3 overflow-hidden rounded-full bg-gray-100"
          aria-label={
            data?.storage_box_used_bytes != null
              ? 'Storage-Box-Belegung'
              : 'Storage-Box-Belegung nicht verfügbar'
          }
          role="img"
        >
          {data?.storage_box_used_bytes != null &&
          data.storage_box_total_bytes != null &&
          data.storage_box_total_bytes > 0 ? (
            <div
              className="h-full rounded-full bg-emerald-500"
              style={{
                width: `${Math.min(100, (data.storage_box_used_bytes / data.storage_box_total_bytes) * 100)}%`,
              }}
            />
          ) : null}
        </div>
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs text-gray-500">{stampedAt}</p>
          <button
            type="button"
            onClick={requestCopy}
            disabled={!data?.manual_copy_available || requesting}
            className="rounded-xl bg-emerald-700 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-emerald-800 disabled:cursor-not-allowed disabled:bg-gray-300 disabled:text-gray-600"
          >
            {requesting ? 'Wird gestartet …' : 'WAV-Dateien jetzt kopieren'}
          </button>
        </div>
        <p className="mt-2 text-xs text-gray-500">
          {data?.manual_copy_available
            ? 'Es werden nur Kopien erstellt; lokale Originale bleiben erhalten.'
            : data?.environment === 'production' || data?.environment === 'prod'
              ? 'Manueller Start ist gesperrt, bis der sichere Production-Dienst angebunden ist.'
              : 'Kopieren ist in Staging deaktiviert und wird erst nach Production-Rollout freigeschaltet.'}
        </p>
        {notice && (
          <p role="status" className="mt-3 text-sm text-gray-700">
            {notice}
          </p>
        )}
      </div>
    </section>
  );
}
