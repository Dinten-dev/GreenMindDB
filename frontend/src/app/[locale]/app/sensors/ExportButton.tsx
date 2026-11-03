'use client';

import { useEffect, useId, useRef, useState } from 'react';
import { useAppText } from '@/i18n/app-text';
import {
  apiDownloadWavBundle,
  apiExportSensorData,
  type SensorDataResponse,
  type CaptureTimeInfo,
} from '@/lib/api';
import { formatCaptureTime } from '@/lib/capture-time';
import SignalChart from './SignalChart';
import { RecordingTimeDetails } from './CaptureTimeSummary';
import RecordingCompletenessBadge from './RecordingCompletenessBadge';
import { nominalCompleteness } from '@/lib/recordingCompleteness';
import type { RecordingCompleteness } from '@/lib/api';

export type ExportDevice = { id: string; kind: 'gateway' | 'direct'; name: string };
type Bounds = { from_dt: string; to_dt: string };
type Recording = {
  segment_id: string;
  revision: number;
  run: number;
  started_at: string;
  duration_seconds: number;
  sample_rate: number;
  completeness?: RecordingCompleteness;
  time_info?: CaptureTimeInfo;
};
const identity = (device: ExportDevice) => `${device.kind}:${device.id}`;
const inputStamp = (value: string) => new Date(value).toISOString().slice(0, 23);

async function read<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(path, { credentials: 'include', cache: 'no-store', signal });
  if (!response.ok)
    throw new Error(
      response.status === 413
        ? 'Export fehlgeschlagen. Bitte Zeitraum eingrenzen oder erneut versuchen.'
        : 'Exportdaten konnten nicht geladen werden. Bitte erneut versuchen.'
    );
  return response.json();
}
async function download(path: string) {
  const response = await fetch(path, { credentials: 'include' });
  if (!response.ok)
    throw new Error('Export fehlgeschlagen. Bitte Zeitraum eingrenzen oder erneut versuchen.');
  const url = URL.createObjectURL(await response.blob());
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download =
    response.headers.get('Content-Disposition')?.match(/filename="?([^";]+)"?/)?.[1] ||
    'greenmind.csv';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function ExportForm({
  devices,
  initialKey,
  initialBounds,
}: {
  devices: ExportDevice[];
  initialKey?: string;
  initialBounds?: Bounds;
}) {
  const tx = useAppText();
  const [key, setKey] = useState(initialKey || (devices[0] ? identity(devices[0]) : ''));
  const [from, setFrom] = useState(() =>
    inputStamp(initialBounds?.from_dt || new Date(Date.now() - 86400000).toISOString())
  );
  const [to, setTo] = useState(() => inputStamp(initialBounds?.to_dt || new Date().toISOString()));
  const [format, setFormat] = useState('csv');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [complete, setComplete] = useState(false);
  const [files, setFiles] = useState<Recording[] | null>(null);
  const [preview, setPreview] = useState<SensorDataResponse[] | null>(null);
  const [action, setAction] = useState<'preview' | 'files' | null>(null);
  const id = useId();
  const device = devices.find((d) => identity(d) === key);
  const start = Date.parse(from + 'Z'),
    end = Date.parse(to + 'Z');
  const valid =
    !!device &&
    Number.isFinite(start) &&
    Number.isFinite(end) &&
    end > start &&
    end - start <= 3650 * 86400000;
  const bounds = valid
    ? { from_dt: new Date(start).toISOString(), to_dt: new Date(end).toISOString() }
    : null;
  const base =
    device?.kind === 'direct'
      ? `/api/v1/visualization/direct/${device.id}`
      : `/api/v1/sensors/${device?.id}`;
  const query = new URLSearchParams({ range: '24h', ...(bounds || {}) }).toString();
  // Changing selection aborts reads and discards previously authorized/cached results.
  useEffect(() => {
    setFiles(null);
    setPreview(null);
    setError('');
    setComplete(false);
    setAction(null);
  }, [key, from, to, format, device?.id]);
  useEffect(() => {
    if (!action || !valid) return;
    const controller = new AbortController();
    setBusy(true);
    setError('');
    const path = `${base}/${action === 'preview' ? 'data' : 'recordings'}?${query}`;
    read<SensorDataResponse[] | Recording[]>(path, controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return;
        if (action === 'preview') setPreview(result as SensorDataResponse[]);
        else setFiles(result as Recording[]);
      })
      .catch((failure) => {
        if (!controller.signal.aborted) {
          setFiles(null);
          setPreview(null);
          setError(tx.error(failure));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setBusy(false);
          setAction(null);
        }
      });
    return () => {
      controller.abort();
      setBusy(false);
    };
  }, [action, valid, base, query, tx]);
  const submit = async () => {
    if (!device || !bounds) return;
    if (format === 'wav' && device.kind === 'direct') {
      setAction('files');
      return;
    }
    setBusy(true);
    setError('');
    setComplete(false);
    try {
      if (format === 'wav') await apiDownloadWavBundle(device.id, bounds.from_dt, bounds.to_dt);
      else if (device.kind === 'gateway') await apiExportSensorData(device.id, '24h', bounds);
      else await download(`${base}/export?${query}`);
      setComplete(true);
    } catch {
      setError(tx('Export fehlgeschlagen. Bitte Zeitraum eingrenzen oder erneut versuchen.'));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-4">
      <label className="block text-sm font-medium" htmlFor={`${id}-device`}>
        {tx('Gerät')}
        <select
          id={`${id}-device`}
          value={key}
          onChange={(e) => setKey(e.target.value)}
          disabled={busy}
          className="mt-1 block w-full rounded-lg border border-gray-200 bg-white p-2"
        >
          {devices.map((d) => (
            <option key={identity(d)} value={identity(d)}>
              {d.name}
            </option>
          ))}
        </select>
      </label>
      <p className="text-xs text-gray-500">{tx('Zeitzone: {value0}', { value0: 'UTC' })}</p>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-sm" htmlFor={`${id}-from`}>
          {tx('Von')}
          <input
            id={`${id}-from`}
            type="datetime-local"
            step="0.001"
            required
            value={from}
            disabled={busy}
            onChange={(e) => setFrom(e.target.value)}
            className="mt-1 block w-full rounded-lg border border-gray-200 p-2"
          />
        </label>
        <label className="text-sm" htmlFor={`${id}-to`}>
          {tx('Bis')}
          <input
            id={`${id}-to`}
            type="datetime-local"
            step="0.001"
            required
            value={to}
            disabled={busy}
            onChange={(e) => setTo(e.target.value)}
            className="mt-1 block w-full rounded-lg border border-gray-200 p-2"
          />
        </label>
      </div>
      {!valid && (
        <p role="alert" className="text-sm text-amber-900">
          {tx('Bitte Gerät und einen gültigen Zeitraum bis zehn Jahre wählen.')}
        </p>
      )}
      <label className="block text-sm" htmlFor={`${id}-format`}>
        {tx('Format')}
        <select
          id={`${id}-format`}
          value={format}
          disabled={busy}
          onChange={(e) => setFormat(e.target.value)}
          className="ml-3 rounded-lg border border-gray-200 p-2"
        >
          <option value="csv">{tx('CSV · Übersicht')}</option>
          <option value="wav">{tx('WAV · Originalaufnahmen')}</option>
        </select>
      </label>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={!valid || busy}
          onClick={() => void submit()}
          className="rounded-lg bg-emerald-700 px-4 py-2 text-sm text-white disabled:opacity-50"
        >
          {busy
            ? tx('Lade…')
            : format === 'wav' && device?.kind === 'direct'
              ? tx('Originalaufnahmen auswählen')
              : tx('Herunterladen')}
        </button>
        <button
          type="button"
          disabled={!valid || busy}
          onClick={() => setAction('preview')}
          className="rounded-lg border border-emerald-200 px-4 py-2 text-sm text-emerald-800 disabled:opacity-50"
        >
          {tx('Verlauf anzeigen')}
        </button>
      </div>
      {error && (
        <p role="alert" className="text-sm text-amber-900">
          {error}
        </p>
      )}
      {complete && (
        <p role="status" className="text-sm text-emerald-800">
          {tx('Download gestartet.')}
        </p>
      )}
      {device && preview && (
        <section aria-label={tx('Historischer Verlauf')} className="space-y-3">
          <h3 className="font-medium">{tx('Historischer Verlauf')}</h3>
          {!preview.some((item) => item.data.length) && (
            <p>{tx('Keine Messdaten für diesen Zeitraum vorhanden')}</p>
          )}
          {preview.map((series) => (
            <SignalChart
              key={series.kind}
              series={series}
              color="#047857"
              unit={series.unit}
              formatTick={(stamp) => formatCaptureTime(stamp, tx.locale)}
            />
          ))}
        </section>
      )}
      {device && files && (
        <div>
          <p className="text-xs text-gray-500">
            {tx(
              'Die neuesten 100 Originalaufnahmen im gewählten Zeitraum. Für ältere Dateien den Zeitraum eingrenzen.'
            )}
          </p>
          {!files.length && (
            <p className="mt-2 text-sm">
              {tx('Noch keine abgeschlossene Aufnahme in diesem Zeitraum.')}
            </p>
          )}
          <ul className="mt-2 max-h-64 divide-y overflow-auto">
            {files.map((file) => (
              <li key={`${file.segment_id}-${file.revision}-${file.run}`} className="py-3 text-sm">
                <a
                  className="text-emerald-800 underline"
                  href={`${base}/wav/${file.segment_id}/${file.revision}/${file.run}`}
                >
                  {formatCaptureTime(file.started_at, tx.locale)} · {tx('WAV herunterladen')}
                </a>
                <RecordingCompletenessBadge completeness={nominalCompleteness(file, true)} />
                <RecordingTimeDetails time={file.time_info} />
              </li>
            ))}
          </ul>
        </div>
      )}

      <details className="text-xs text-gray-500">
        <summary className="cursor-pointer">{tx('Technische Details')}</summary>
        <p className="mt-2">
          {tx(
            'CSV enthält verdichtete Messwerte. WAV enthält unveränderte Originalaufnahmen; überlappende Dateien können über den gewählten Zeitraum hinausreichen.'
          )}
        </p>
        <p>{tx('Markierungen bleiben nur live sichtbar und werden nicht exportiert.')}</p>
      </details>
    </div>
  );
}

export default function ExportButton(props: {
  devices: ExportDevice[];
  initialKey?: string;
  initialBounds?: Bounds;
}) {
  const tx = useAppText();
  const [open, setOpen] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const title = useId();
  useEffect(() => {
    if (open && !dialog.current?.open) dialog.current?.showModal();
    if (!open && dialog.current?.open) dialog.current.close();
  }, [open]);
  return (
    <>
      <button
        type="button"
        disabled={!props.devices.length}
        onClick={() => setOpen(true)}
        className="rounded-lg border border-emerald-200 bg-white px-4 py-2 text-sm font-medium text-emerald-800 disabled:opacity-50"
      >
        {tx('Exportieren')}
      </button>
      <dialog
        ref={dialog}
        aria-labelledby={title}
        onClose={() => setOpen(false)}
        onCancel={() => setOpen(false)}
        onClick={(event) => {
          if (event.target === event.currentTarget) setOpen(false);
        }}
        className="m-auto max-h-[90dvh] w-[calc(100%_-_2rem)] max-w-3xl overflow-y-auto rounded-2xl bg-white p-5 shadow-xl backdrop:bg-black/30 sm:p-6"
      >
        <div className="mb-5 flex items-center justify-between gap-3">
          <h2 id={title} className="text-lg font-semibold">
            {tx('Exportieren')}
          </h2>
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="rounded-lg px-3 py-2 text-sm text-gray-600"
          >
            {tx('Schliessen')}
          </button>
        </div>
        {open && <ExportForm {...props} />}
      </dialog>
    </>
  );
}
