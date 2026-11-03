'use client';

import { useAppText } from '@/i18n/app-text';

import { useEffect, useState } from 'react';
import { apiListWavFiles, type WavFileInfo } from '@/lib/api';
import RecordingCompletenessBadge from './RecordingCompletenessBadge';
import { nominalCompleteness } from '@/lib/recordingCompleteness';

export default function WavCompletenessList({
  sensorId,
  fromDate,
  toDate,
  from,
  to,
}: {
  sensorId: string;
  fromDate: string;
  toDate: string;
  from?: string;
  to?: string;
}) {
  const tx = useAppText();

  const [files, setFiles] = useState<WavFileInfo[]>([]);
  const [failed, setFailed] = useState(false);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let current = true;
    setLoading(true);
    setFailed(false);
    setFiles([]);
    let pending = false;
    const refresh = () => {
      if (pending) return;
      pending = true;
      return apiListWavFiles(sensorId, {
        from_dt: from ?? `${fromDate}T00:00:00Z`,
        to_dt: to ?? `${toDate}T23:59:59Z`,
        limit: 100,
      })
        .then((rows) => {
          if (current) {
            setFiles(rows.map((file) => ({ ...file, completeness: nominalCompleteness(file) })));
            setFailed(false);
          }
        })
        .catch(() => {
          if (current) setFailed(true);
        })
        .finally(() => {
          pending = false;
          if (current) setLoading(false);
        });
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 30_000);
    return () => {
      current = false;
      window.clearInterval(timer);
    };
  }, [sensorId, fromDate, toDate, from, to]);
  const short = files.filter((file) =>
    ['short', 'partial'].includes(file.completeness?.status || '')
  ).length;
  return (
    <section
      className="mt-6 border-t border-black/[0.04] pt-5"
      aria-label={tx('Vollständigkeit der Aufnahmen')}
    >
      <h3 className="text-sm font-medium text-gray-800">{tx('Vollständigkeit der Aufnahmen')}</h3>
      <p className="mt-1 text-xs text-gray-500">
        {tx(
          'Online beschreibt die Verbindung. Diese Prüfung beschreibt die gespeicherten Aufnahmen im gewählten Zeitraum. Ältere Dateien werden am Zehnminutenfenster geprüft; der Grund einer Verkürzung bleibt unbekannt.'
        )}
      </p>
      {loading ? (
        <p className="mt-2 text-xs text-gray-500">{tx('Aufnahmen werden geprüft…')}</p>
      ) : failed ? (
        <p role="status" className="mt-2 text-xs text-amber-800">
          {tx('Vollständigkeit momentan ungeprüft.')}
        </p>
      ) : files.length === 0 ? (
        <p className="mt-2 text-xs text-gray-500">{tx('Keine Aufnahmen im gewählten Zeitraum.')}</p>
      ) : (
        <>
          {short > 0 && (
            <p role="status" className="mt-2 text-sm text-amber-800">
              {tx(
                '{count, plural, one {# verkürzte oder unvollständige Aufnahme} other {# verkürzte oder unvollständige Aufnahmen}}',
                { count: short }
              )}{' '}
              {tx('in dieser Liste.')}
            </p>
          )}
          <ul className="mt-2 max-h-64 divide-y divide-gray-100 overflow-auto">
            {files.map((file) => (
              <li key={file.id} className="py-2 text-xs text-gray-600">
                {new Date(file.started_at).toLocaleString(tx.locale)}
                <RecordingCompletenessBadge completeness={file.completeness} />
              </li>
            ))}
          </ul>
          {files.length === 100 && (
            <p className="mt-2 text-xs text-gray-500">
              {tx('Die neuesten 100 Dateien. Zeitraum eingrenzen, um ältere Dateien zu prüfen.')}
            </p>
          )}
        </>
      )}
    </section>
  );
}
