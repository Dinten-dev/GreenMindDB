'use client';

import { useAppText } from '@/i18n/app-text';

import { useEffect, useState } from 'react';

type Job = {
  id: string;
  status: 'queued' | 'working' | 'ready' | 'failed' | 'cancelled';
  files?: number;
  bytes?: number;
  completed?: number;
  total?: number;
  parts?: number;
  error?: string | null;
};

export default function ArchiveExportPanel({
  kind,
  sensorId,
  from,
  to,
}: {
  kind: 'gateway' | 'direct';
  sensorId: string;
  from: string;
  to: string;
}) {
  const tx = useAppText();

  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState('');
  const jobId = job?.id;
  const jobStatus = job?.status;

  useEffect(() => {
    if (!jobId || !['queued', 'working'].includes(jobStatus ?? '')) return;
    let active = true;
    const timer = setInterval(async () => {
      try {
        const response = await fetch(`/api/v1/visualization/archive-exports/${jobId}`, {
          credentials: 'include',
          cache: 'no-store',
        });
        if (!response.ok) throw new Error(tx('Exportstatus nicht verfügbar'));
        const current = (await response.json()) as Job;
        if (active) setJob((previous) => ({ ...previous, ...current }));
      } catch {
        if (active) setError(tx('Exportstatus vorübergehend nicht erreichbar.'));
      }
    }, 5000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [tx, jobId, jobStatus]);

  if (process.env.NEXT_PUBLIC_ARCHIVE_EXPORTS_ENABLED !== 'true') return null;

  const prepare = async () => {
    setError('');
    try {
      const params = new URLSearchParams({ kind, sensor_id: sensorId, from_dt: from, to_dt: to });
      const response = await fetch(`/api/v1/visualization/archive-exports?${params}`, {
        method: 'POST',
        credentials: 'include',
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || 'Export konnte nicht erstellt werden');
      }
      setJob((await response.json()) as Job);
    } catch (cause) {
      setError(tx.error(cause));
    }
  };

  return (
    <div className="rounded-xl border border-emerald-100 bg-[#f7faf5] p-3 text-sm">
      <p className="font-medium text-emerald-900">{tx('Original-WAVs für Analysen')}</p>
      <p className="mt-1 text-xs text-gray-600">
        {tx('Originaldateien und Prüfsummen für den ausgewählten Sensor und Zeitraum.')}
      </p>
      <button
        type="button"
        onClick={prepare}
        disabled={job?.status === 'queued' || job?.status === 'working'}
        className="mt-2 rounded-lg bg-emerald-700 px-3 py-2 font-medium text-white disabled:opacity-50"
      >
        {tx('Download vorbereiten')}
      </button>
      {job && (
        <p role="status" className="mt-2 text-xs text-gray-600">
          {job.status === 'ready'
            ? tx('Bereit')
            : job.status === 'failed'
              ? tx('Fehlgeschlagen')
              : job.status === 'working'
                ? tx('Prüfen: {value0} von {value1}', {
                    value0: job.completed ?? 0,
                    value1: job.total ?? job.files ?? 0,
                  })
                : tx('Wartet auf freie Kapazität')}
        </p>
      )}
      {job?.status === 'ready' && (
        <div className="mt-2 flex flex-wrap gap-2">
          {Array.from({ length: job.parts ?? 0 }, (_, index) => (
            <a
              key={index}
              href={`/api/v1/visualization/archive-exports/${job.id}/parts/${index}`}
              className="rounded-lg border border-emerald-200 bg-white px-3 py-2 text-emerald-800"
            >
              {tx('ZIP-Teil')}
              {index + 1} {tx('herunterladen')}
            </a>
          ))}
        </div>
      )}
      {(error || job?.error) && (
        <div className="mt-2 text-amber-800">
          <p role="alert">{error || tx('Export konnte nicht erstellt werden')}</p>
          {job?.error && (
            <details className="mt-1 text-xs">
              <summary>{tx('Technische Details')}</summary>
              <pre className="whitespace-pre-wrap break-words">{job.error}</pre>
            </details>
          )}
        </div>
      )}
    </div>
  );
}
