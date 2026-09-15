'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { apiGetSensorWaveform, SensorDataResponse } from '@/lib/api';
import { prepareSignalSeries, resolutionLabel } from '@/lib/signal-series';

export default function SignalChart({
  series,
  color,
  unit,
  formatTick,
}: {
  series: SensorDataResponse;
  color: string;
  unit: string;
  formatTick: (timestamp: string) => string;
}) {
  const [metric, setMetric] = useState<'value' | 'rms'>('value');
  const [selectedAt, setSelectedAt] = useState<string | null>(null);
  const [waveform, setWaveform] = useState<Awaited<ReturnType<typeof apiGetSensorWaveform>> | null>(
    null
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const generation = useRef(0);
  const firstTimestamp = series.data[0]?.timestamp;
  useEffect(() => {
    generation.current += 1;
    setSelectedAt(null);
    setWaveform(null);
    setError('');
    setLoading(false);
  }, [series.sensor_id, firstTimestamp]);
  const points = useMemo(() => prepareSignalSeries(series.data, metric), [series.data, metric]);
  const verified = series.data.some((point) => point.signal_source === 'wav');
  const availableRms = series.data.some((point) => point.rms != null);
  const selected = selectedAt ?? series.data[Math.floor(series.data.length * 0.75)]?.timestamp;
  const preview = async () => {
    if (!selected) return;
    const requestGeneration = generation.current;
    setLoading(true);
    setError('');
    setWaveform(null);
    try {
      const result = await apiGetSensorWaveform(series.sensor_id, selected);
      if (requestGeneration === generation.current) setWaveform(result);
    } catch (failure) {
      if (requestGeneration !== generation.current) return;
      setError(
        failure instanceof Error ? failure.message : 'Originalsignal derzeit nicht verfügbar.'
      );
    } finally {
      if (requestGeneration === generation.current) setLoading(false);
    }
  };

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-xs text-gray-500">
        <span>Zeitfenster: {resolutionLabel(series.data)}</span>
        {availableRms && (
          <div className="flex rounded-lg bg-[#f3f6f0] p-1" aria-label="Darstellung">
            {(['value', 'rms'] as const).map((choice) => (
              <button
                key={choice}
                type="button"
                aria-pressed={metric === choice}
                className={`rounded-md px-3 py-1.5 ${metric === choice ? 'bg-white text-[#236b46] shadow-sm' : ''}`}
                onClick={() => setMetric(choice)}
              >
                {choice === 'value' ? 'Verlauf' : 'Signalstärke (RMS)'}
              </button>
            ))}
          </div>
        )}
      </div>
      <ResponsiveContainer width="100%" height={240}>
        <ComposedChart
          data={points}
          accessibilityLayer
          margin={{ top: 12, right: 16, bottom: 8, left: 0 }}
          onClick={(event) => {
            const stamp = event?.activePayload?.[0]?.payload?.timestamp;
            if (typeof stamp === 'string') setSelectedAt(stamp);
          }}
        >
          <CartesianGrid stroke="var(--color-border-light)" vertical={false} />
          <XAxis
            dataKey="time"
            type="number"
            scale="time"
            domain={['dataMin', 'dataMax']}
            tickFormatter={(time) => formatTick(new Date(time).toISOString())}
            tick={{ fontSize: 12 }}
            tickLine={false}
            minTickGap={40}
          />
          <YAxis
            width={64}
            domain={['auto', 'auto']}
            tick={{ fontSize: 12 }}
            tickLine={false}
            label={{ value: unit, angle: -90, position: 'insideLeft', style: { fontSize: 12 } }}
          />
          <Tooltip
            content={({ active, payload }) => {
              if (!active || !payload?.length) return null;
              const point = payload[0].payload;
              return (
                <div className="rounded-xl border border-gray-100 bg-white p-3 text-xs shadow-lg">
                  <p className="mb-1 text-gray-500">
                    {new Date(point.timestamp).toLocaleString('de-CH')}
                  </p>
                  <p>
                    {metric === 'value' ? 'Mittelwert' : 'Signalstärke'}:{' '}
                    {point.plotted?.toFixed(2) ?? '—'} {unit}
                  </p>
                  {point.envelope && (
                    <p>
                      Ausschläge: {point.envelope[0].toFixed(2)}–{point.envelope[1].toFixed(2)}{' '}
                      {unit}
                    </p>
                  )}
                  {point.coverage_ratio != null && (
                    <p>Datenabdeckung: {(point.coverage_ratio * 100).toFixed(1)} %</p>
                  )}
                </div>
              );
            }}
          />
          <Area
            type="linear"
            dataKey="envelope"
            name="Ausschlagsbereich"
            stroke="none"
            fill={color}
            fillOpacity={0.16}
            connectNulls={false}
            isAnimationActive={false}
          />
          <Line
            type="linear"
            dataKey="plotted"
            name={metric === 'value' ? 'Mittelwert' : 'Signalstärke'}
            stroke={color}
            strokeWidth={2}
            dot={false}
            connectNulls={false}
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
      <p className="mt-2 text-xs leading-relaxed text-gray-500">
        {metric === 'rms'
          ? 'Die Linie zeigt den quadratischen Mittelwert der erfassten Signalwerte. '
          : verified
            ? 'Die Linie zeigt den Mittelwert, das Band die kleinsten und größten Werte aus den geprüften WAV-Aufnahmen. '
            : series.original_signal_available
              ? 'Die Linie zeigt die vorhandenen Messwerte. Original-Ausschläge erscheinen bei zeitlich zuordenbaren WAV-Aufnahmen nach deren Auswertung. '
              : 'Die Linie zeigt den Mittelwert, das Band die kleinsten und größten Messwerte. '}
        Datenlücken werden nicht überbrückt.
      </p>
      {series.original_signal_available && (
        <div className="mt-4 border-t border-gray-100 pt-3">
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              disabled={loading || !selected}
              onClick={preview}
              className="rounded-lg bg-[#f3f6f0] px-3 py-2 text-xs font-medium text-[#236b46] disabled:opacity-50"
            >
              {loading ? 'Originalsignal wird geladen …' : '10 Sekunden Originalsignal ansehen'}
            </button>
            <span className="text-xs text-gray-500">
              Zeitpunkt durch Anklicken im Diagramm wählen.
            </span>
          </div>
          {selected && (
            <p className="mt-2 text-xs text-gray-500">
              Auswahl: {new Date(selected).toLocaleString('de-CH')}
            </p>
          )}
          {error && (
            <p role="status" className="mt-2 text-xs text-amber-700">
              {error}
            </p>
          )}
          {waveform && (
            <section aria-label="Originalsignal" className="mt-3 rounded-xl bg-[#f7f9f5] p-3">
              <p className="mb-2 text-xs text-[#236b46]">
                Originalaufnahme · {waveform.sample_rate} Messungen pro Sekunde · {waveform.unit}
              </p>
              <ResponsiveContainer width="100%" height={180}>
                <ComposedChart data={waveform.data}>
                  <XAxis
                    dataKey="timestamp"
                    tickFormatter={(stamp) => new Date(stamp).toLocaleTimeString('de-CH')}
                    minTickGap={40}
                    tick={{ fontSize: 11 }}
                  />
                  <YAxis domain={['auto', 'auto']} width={60} tick={{ fontSize: 11 }} />
                  <Tooltip labelFormatter={(stamp) => new Date(String(stamp)).toISOString()} />
                  <Line
                    dataKey="value"
                    type="linear"
                    stroke={color}
                    dot={false}
                    isAnimationActive={false}
                  />
                </ComposedChart>
              </ResponsiveContainer>
            </section>
          )}
        </div>
      )}
    </div>
  );
}
