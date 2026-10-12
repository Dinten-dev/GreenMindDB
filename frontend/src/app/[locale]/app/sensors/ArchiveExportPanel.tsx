'use client';

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
        if (!response.ok) throw new Error('Exportstatus nicht verfügbar');
        const current = (await response.json()) as Job;
        if (active) setJob((previous) => ({ ...previous, ...current }));
      } catch {
        if (active) setError('Exportstatus vorübergehend nicht erreichbar.');
      }
    }, 5000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [jobId, jobStatus]);

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
      setError(cause instanceof Error ? cause.message : 'Export fehlgeschlagen');
    }
  };

  return (
    <div className="rounded-xl border border-emerald-100 bg-[#f7faf5] p-3 text-sm">
      <p className="font-medium text-emerald-900">Original-WAVs für Analysen</p>
      <p className="mt-1 text-xs text-gray-600">
        Originaldateien und Prüfsummen für den ausgewählten Sensor und Zeitraum.
      </p>
      <button
        type="button"
        onClick={prepare}
        disabled={job?.status === 'queued' || job?.status === 'working'}
        className="mt-2 rounded-lg bg-emerald-700 px-3 py-2 font-medium text-white disabled:opacity-50"
      >
        Download vorbereiten
      </button>
      {job && (
        <p role="status" className="mt-2 text-xs text-gray-600">
          {job.status === 'ready'
            ? 'Bereit'
            : job.status === 'failed'
              ? 'Fehlgeschlagen'
              : job.status === 'working'
                ? `Prüfen: ${job.completed ?? 0} von ${job.total ?? job.files ?? 0}`
                : 'Wartet auf freie Kapazität'}
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
              ZIP-Teil {index + 1} herunterladen
            </a>
          ))}
        </div>
      )}
      {(error || job?.error) && (
        <p role="alert" className="mt-2 text-amber-800">
          {error || job?.error}
        </p>
      )}
    </div>
  );
}
