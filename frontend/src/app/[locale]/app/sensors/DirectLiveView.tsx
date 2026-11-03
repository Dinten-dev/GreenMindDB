'use client';

import { useEffect, useState } from 'react';
import { readViewValue, replaceViewValues } from '@/lib/view-state';
import { useDirectLive, type LiveSnapshot } from '@/hooks/useDirectLive';
import { captureTimeZone, formatCaptureTime } from '@/lib/capture-time';
import { useAppText } from '@/i18n/app-text';

function LiveCurve({ data, seconds }: { data: LiveSnapshot; seconds: number }) {
  const tx = useAppText();
  const right = data.server_us;
  const left = right - seconds * 1000000;
  const points = data.points.filter((p) => p.recorded_us >= left);
  const low = Math.min(...points.map((p) => p.mv), 3300);
  const high = Math.max(...points.map((p) => p.mv), 0);
  const padding = Math.max(1, (high - low) * 0.1);
  const minimum = Math.max(0, low - padding),
    maximum = Math.min(3300, high + padding);
  const x = (stamp: number) => 55 + (690 * (stamp - left)) / (right - left);
  const y = (mv: number) => 195 - (170 * (mv - minimum)) / Math.max(1, maximum - minimum);
  let path = '';
  points.forEach((point, index) => {
    const previous = points[index - 1];
    const command = previous && point.frame - previous.frame === point.samples ? 'L' : 'M';
    path += `${command}${x(point.recorded_us).toFixed(2)},${y(point.mv).toFixed(2)} `;
  });
  const format = (stamp: number) =>
    new Date(stamp / 1000).toLocaleTimeString(tx.locale, { timeZoneName: 'shortOffset' });
  return (
    <svg
      role="img"
      aria-label={tx('Live-Vorschau')}
      viewBox="0 0 780 235"
      className="w-full min-h-48 rounded-xl bg-white"
    >
      <text x="6" y="15" className="fill-gray-500 text-[12px]">
        {tx('mV')}
      </text>
      {[minimum, maximum].map((value, i) => (
        <g key={i}>
          <line x1="55" x2="745" y1={y(value)} y2={y(value)} stroke="#e5e7eb" />
          <text x="48" y={y(value) + 4} textAnchor="end" className="fill-gray-500 text-[12px]">
            {value.toLocaleString(tx.locale, { maximumFractionDigits: 1 })}
          </text>
        </g>
      ))}
      <path d={path} fill="none" stroke="#047857" strokeWidth="2" />
      {data.markers
        .filter((m) => m.at_us >= left && m.at_us <= right)
        .map((marker) => (
          <g key={marker.id}>
            <title>
              {tx('Markierung')} · {format(marker.at_us)}
            </title>
            <line
              x1={x(marker.at_us)}
              x2={x(marker.at_us)}
              y1="24"
              y2="196"
              stroke="#b45309"
              strokeDasharray="4 3"
            />
            <text x={x(marker.at_us) + 3} y="20" className="fill-amber-800 text-[12px]">
              {tx('Mark')}
            </text>
          </g>
        ))}
      <text x="55" y="222" className="fill-gray-500 text-[12px]">
        {format(left)}
      </text>
      <text x="745" y="222" textAnchor="end" className="fill-gray-500 text-[12px]">
        {format(right)}
      </text>
    </svg>
  );
}

export default function DirectLiveView({
  deviceId,
  onHistory,
  showHistoryButton = true,
}: {
  deviceId: string;
  onHistory: () => void;
  showHistoryButton?: boolean;
}) {
  const tx = useAppText();
  const [seconds, setSeconds] = useState<30 | 60 | 180>(() => {
    const value = readViewValue('directLiveWindow');
    return value === '60' ? 60 : value === '180' ? 180 : 30;
  });
  useEffect(() => {
    replaceViewValues({ directLiveWindow: seconds === 30 ? null : String(seconds) });
  }, [seconds]);
  const live = useDirectLive(deviceId, seconds);
  const snapshot = live.snapshot;
  const rail = snapshot?.rail;
  const highRail = rail?.percent !== null && rail?.percent !== undefined && rail.percent > 5;
  return (
    <section
      aria-label={tx('Live-Vorschau')}
      className="space-y-4 rounded-xl bg-[#fcfdfb] p-3 sm:p-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="font-semibold text-gray-900">{tx('Live-Vorschau')}</h3>
        <div className="flex gap-2">
          {showHistoryButton && (
            <button
              type="button"
              onClick={onHistory}
              className="rounded-lg border border-gray-200 px-3 py-2 text-sm"
            >
              {tx('24 Stunden')}
            </button>
          )}
          <button
            type="button"
            disabled={live.stale || live.marking || live.paused}
            title={tx('Markierung am zuletzt empfangenen Messpunkt setzen.')}
            onClick={() => void live.mark()}
            className="rounded-lg bg-emerald-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            {tx('Mark')}
          </button>
        </div>
      </div>
      <div className="flex flex-wrap gap-2" aria-label={tx('Livefenster')}>
        {([30, 60, 180] as const).map((value) => (
          <button
            type="button"
            key={value}
            aria-pressed={seconds === value}
            onClick={() => setSeconds(value)}
            className={`rounded-lg border px-3 py-2 text-sm ${seconds === value ? 'border-emerald-700 bg-emerald-700 text-white' : 'border-gray-200 bg-white'}`}
          >
            {tx(value === 30 ? '30 Sekunden' : value === 60 ? '1 Minute' : '3 Minuten')}
          </button>
        ))}
      </div>
      {live.error && (
        <p role="alert" className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900">
          {live.error}{' '}
          <button type="button" onClick={live.retry} className="underline">
            {tx('Erneut laden')}
          </button>
        </p>
      )}
      {live.paused ? (
        <p role="status">{tx('Live pausiert, solange dieser Tab ausgeblendet ist.')}</p>
      ) : (
        <>
          {snapshot?.capability === 'upgrade_required' ? (
            <p role="status" className="text-sm text-amber-900">
              {tx('Live benötigt eine geeignete Firmware. Aktualisierung bitte abstimmen.')}
            </p>
          ) : !snapshot?.points.length ? (
            <p role="status" className="py-8 text-sm text-gray-500">
              {tx('Warte auf Live-Daten …')}
            </p>
          ) : (
            <LiveCurve data={snapshot} seconds={seconds} />
          )}
          {live.stale && !!snapshot?.points.length && (
            <p role="status" className="text-sm text-amber-900">
              {tx('Live-Signal veraltet')}
            </p>
          )}
          {snapshot && !snapshot.window_complete && (
            <p className="text-xs text-amber-900">{tx('Livefenster unvollständig')}</p>
          )}
          <div
            className={`rounded-xl border p-3 text-sm ${highRail && !live.railStale ? 'border-amber-300 bg-amber-50 text-amber-950' : 'border-gray-200 bg-white text-gray-700'}`}
          >
            <p className="font-medium">
              {tx('Rail-Anteil')}:{' '}
              {rail?.percent === null || rail?.percent === undefined
                ? tx('Keine Rail-Messung')
                : `${rail.percent.toLocaleString(tx.locale, { maximumFractionDigits: 1 })} %`}
            </p>
            {live.railStale && <p>{tx('Rail-Messung veraltet')}</p>}
            {rail && (
              <p className="mt-1 text-xs">
                {tx('Rail-Messung bis: {value0}', {
                  value0: formatCaptureTime(new Date(rail.end_us / 1000).toISOString(), tx.locale),
                })}
              </p>
            )}
            {rail && (
              <p className="mt-1 text-xs">
                {tx('{value0} von {value1} Rohwerten', {
                  value0: rail.rail_samples,
                  value1: rail.raw_samples,
                })}{' '}
                ·{' '}
                {tx('Messfenster: {value0} s', {
                  value0: ((rail.end_us - rail.start_us) / 1000000).toLocaleString(tx.locale, {
                    maximumFractionDigits: 1,
                  }),
                })}
              </p>
            )}
            {rail && !rail.complete && (
              <p className="mt-1 text-xs">{tx('Rail-Fenster unvollständig')}</p>
            )}
            {highRail && !live.railStale && (
              <p role="alert" className="mt-2 font-medium">
                {tx('Hoher Rail-Anteil. Elektroden und Referenz prüfen.')}
                <span className="mt-1 block text-xs font-normal">
                  {tx('Übersteuerung ist ein Messproblem und keine Aussage über Pflanzenstress.')}
                </span>
              </p>
            )}
          </div>
          {live.signalAge !== null && (
            <p className="text-xs text-gray-500">
              {tx('Signalalter: {value0} ms', { value0: Math.round(live.signalAge) })}
            </p>
          )}
        </>
      )}
      {live.marked && (
        <p role="status" className="text-sm text-emerald-800">
          {tx('Markierung gesetzt.')}
        </p>
      )}
      <details className="text-xs text-gray-500">
        <summary className="cursor-pointer">{tx('Technische Details')}</summary>
        {snapshot && snapshot.points.length > 0 && (
          <div className="space-y-1 text-xs text-gray-600">
            <p>
              {tx('Aufnahmezeit: {value0} – {value1}', {
                value0: formatCaptureTime(
                  new Date(snapshot.points[0].recorded_us / 1000).toISOString(),
                  tx.locale
                ),
                value1: formatCaptureTime(
                  new Date(
                    snapshot.points[snapshot.points.length - 1].recorded_us / 1000
                  ).toISOString(),
                  tx.locale
                ),
              })}
            </p>
            <p>{tx('Zeitzone: {value0}', { value0: captureTimeZone() })}</p>
            <p>{tx('Zeitgenauigkeit nicht unabhängig geprüft.')}</p>
          </div>
        )}
        <p className="text-xs leading-relaxed text-gray-500">
          {tx(
            'Vorschau: 12,5 Werte pro Sekunde, nominale mV. Originalaufnahmen laufen unabhängig weiter.'
          )}
        </p>
        <p className="text-xs leading-relaxed text-gray-500">
          {tx('Markierungen sind für berechtigte Zuschauer drei Minuten sichtbar. Kein Export.')}
        </p>{' '}
      </details>
    </section>
  );
}
