'use client';

import type { CaptureTimeInfo, SensorDataResponse } from '@/lib/api';
import { captureBounds, captureTimeZone, formatCaptureTime } from '@/lib/capture-time';
import { useAppText } from '@/i18n/app-text';

export function RecordingTimeDetails({ time }: { time?: CaptureTimeInfo }) {
  const tx = useAppText();
  return (
    <details className="mt-1 text-xs text-gray-500">
      <summary className="cursor-pointer">{tx('Zeit- und Integritätsdetails')}</summary>
      <p className="mt-1">
        {time?.anchor_status === 'available'
          ? tx('Zeitanker und nominelle Abtastrate')
          : tx('Aufnahmezeit ungesichert: Zeitanker fehlt oder ist unvollständig.')}
      </p>
      <p>{tx('Zeitgenauigkeit nicht unabhängig geprüft.')}</p>
      {time?.session_id && <p>{tx('Sitzung: {value0}', { value0: time.session_id })}</p>}
      {time?.first_frame !== undefined && (
        <p>{tx('Erstes Sample: {value0}', { value0: time.first_frame })}</p>
      )}
      {time?.sha256 && (
        <p className="break-all">{tx('SHA-256: {value0}', { value0: time.sha256 })}</p>
      )}
      {time?.sha256 && <p>{tx('Dateiintegrität und Zeitgenauigkeit sind getrennte Prüfungen.')}</p>}
    </details>
  );
}

export default function CaptureTimeSummary({ series }: { series: SensorDataResponse }) {
  const tx = useAppText();
  const time = series.time_info;
  const bounds = captureBounds(series);
  const zone = captureTimeZone();
  const qualified = time?.anchor_status === 'available';
  const coverage = time?.coverage_ratio;
  return (
    <section
      aria-label={tx('Aufnahmezeit und Datenabdeckung')}
      className="mb-3 space-y-1 text-xs text-gray-600"
    >
      <h4 className="font-medium text-gray-800">
        {bounds.first && bounds.last
          ? tx('Aufnahmezeit: {value0} – {value1}', {
              value0: formatCaptureTime(bounds.first, tx.locale, zone),
              value1: formatCaptureTime(bounds.last, tx.locale, zone),
            })
          : tx('Aufnahmezeit nicht verfügbar')}
      </h4>
      <p>{tx('Zeitzone: {value0}', { value0: zone })}</p>
      {!qualified && (
        <p role="status" className="text-amber-800">
          {tx('Aufnahmezeit ungesichert: Zeitanker fehlt oder ist unvollständig.')}
        </p>
      )}
      {time?.ongoing && <p role="status">{tx('Aufnahme läuft')}</p>}
      {time?.coverage_status === 'incomplete' && (
        <p role="status" className="text-amber-800">
          {tx('Angezeigtes Zeitfenster unvollständig')}
        </p>
      )}
      {time?.overlap && (
        <p role="status" className="text-amber-800">
          {tx('Überlappende Zeitbereiche: Abdeckung ungeprüft.')}
        </p>
      )}
      <details className="text-xs text-gray-500">
        <summary className="cursor-pointer">{tx('Technische Details')}</summary>
        {qualified && <p>{tx('Zeitanker und nominelle Abtastrate')}</p>}
        <p>{tx('Zeitgenauigkeit nicht unabhängig geprüft.')}</p>
        {coverage !== undefined && coverage !== null ? (
          <p>
            {tx('Datenabdeckung der Übersicht: {value0} %', {
              value0: (coverage * 100).toLocaleString(tx.locale, { maximumFractionDigits: 1 }),
            })}
          </p>
        ) : (
          <p>{tx('Datenabdeckung ungeprüft')}</p>
        )}
        {time?.gap_count !== undefined && time.gap_count > 0 && (
          <p>{tx('Fehlende Zeitbereiche: {value0}', { value0: time.gap_count })}</p>
        )}
        <p>{tx('Die Abdeckung der Übersicht bestätigt keine vollständige Rohaufzeichnung.')}</p>
        <p>{tx('Empfangszeit steht separat im Gerätestatus.')}</p>{' '}
      </details>
    </section>
  );
}
