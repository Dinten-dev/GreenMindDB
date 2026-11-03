'use client';

import { useAppText } from '@/i18n/app-text';

import React, { useState } from 'react';

interface BioSession {
  id: string;
  mac: string;
  total_samples: number;
  invalid_samples: number;
  duration_seconds: number;
  wav_ready: boolean;
}

interface BioSignalPoint {
  timestamp: string;
  value: number;
  invalid: boolean;
}

export default function BioSignalDashboard() {
  const tx = useAppText();

  const [sessionId, setSessionId] = useState<string>('');
  const [sessionInfo, setSessionInfo] = useState<BioSession | null>(null);
  const [signalData, setSignalData] = useState<BioSignalPoint[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const apiBase = '/api/v1/biosignal';

  const fetchSessionData = async () => {
    if (!sessionId) return;
    setLoading(true);
    setError(null);
    try {
      // 1. Fetch metadata
      const resMeta = await fetch(`${apiBase}/sessions/${sessionId}`);
      if (!resMeta.ok) throw new Error(tx('Session not found'));
      const meta = await resMeta.json();
      setSessionInfo(meta);

      // 2. Fetch the aggregated 1-second signal stream
      const resSig = await fetch(`${apiBase}/sessions/${sessionId}/signal`);
      if (!resSig.ok) throw new Error(tx('Error fetching signal'));
      const sig = await resSig.json();
      setSignalData(sig.data);
    } catch (err: unknown) {
      setError(tx.error(err));
    } finally {
      setLoading(false);
    }
  };

  const percentValid = sessionInfo
    ? (
        ((sessionInfo.total_samples - sessionInfo.invalid_samples) / sessionInfo.total_samples) *
        100
      ).toFixed(1)
    : '100';

  return (
    <div className="p-8 max-w-6xl mx-auto space-y-8">
      <div className="flex justify-between items-center bg-white p-6 rounded-2xl shadow-sm border border-gray-100">
        <div>
          <h1 className="text-3xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-emerald-600 to-teal-500">
            {tx('Bio-Signal Monitor')}
          </h1>
          <p className="text-gray-500 mt-2">
            {tx('Experimental research UI; its API is disabled in staging and production.')}
          </p>
        </div>

        <div className="flex gap-4 items-end">
          <div className="flex flex-col">
            <label className="text-sm text-gray-500 mb-1 font-medium">{tx('Session ID')}</label>
            <input
              type="text"
              placeholder={tx('e.g. 550e8400-e29b-41d4-a716-446655440000')}
              className="border border-gray-200 rounded-xl px-4 py-2 focus:ring-2 focus:ring-emerald-500 outline-none w-80 text-gray-700"
              value={sessionId}
              onChange={(e) => setSessionId(e.target.value)}
            />
          </div>
          <button
            onClick={fetchSessionData}
            disabled={loading || !sessionId}
            className="bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white font-medium px-6 py-2 rounded-xl transition-all shadow-sm"
          >
            {loading ? tx('Loading...') : tx('Load Signal')}
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-red-50 text-red-600 p-4 rounded-xl border border-red-100 text-sm font-medium">
          {error}
        </div>
      )}

      {sessionInfo && (
        <div className="grid grid-cols-4 gap-6">
          <StatsCard label={tx('Sensor MAC')} value={sessionInfo.mac} />
          <StatsCard label={tx('Duration')} value={`${sessionInfo.duration_seconds.toFixed(0)}s`} />
          <StatsCard
            label={tx('Signal Quality (Valid)')}
            value={`${percentValid}%`}
            highlight={parseFloat(percentValid) < 95 ? 'text-amber-500' : 'text-emerald-500'}
          />
          <StatsCard
            label={tx('WAV Export Status')}
            value={sessionInfo.wav_ready ? tx('Ready') : tx('Pending / None')}
          />
        </div>
      )}

      {signalData.length > 0 && (
        <div className="bg-white p-6 rounded-2xl shadow-sm border border-gray-100 h-96 flex flex-col justify-end relative">
          <div className="absolute top-6 left-6 text-sm font-semibold text-gray-400 uppercase tracking-wider">
            {tx('Output Voltage (mV) — 1s Aggregates')}
          </div>
          <div className="flex items-end h-full gap-[2px] mt-8 overflow-x-auto w-full">
            {signalData.map((pt, i) => {
              // Map voltage range [0, 3300] to a percentage [0, 100] for height
              const heightPct = Math.max(0, Math.min(100, (pt.value / 3300) * 100));
              return (
                <div
                  key={i}
                  className={`w-2 transition-all rounded-t-sm flex-shrink-0 ${
                    pt.invalid ? 'bg-red-200' : 'bg-emerald-400'
                  }`}
                  style={{ height: `${heightPct}%` }}
                  title={tx('{value0}: {value1}mV {value2}', {
                    value0: new Date(pt.timestamp).toLocaleTimeString(tx.locale),
                    value1: pt.value.toFixed(1),
                    value2: pt.invalid ? tx('(Contains Artifacts)') : '',
                  })}
                ></div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function StatsCard({
  label,
  value,
  highlight = 'text-gray-900',
}: {
  label: string;
  value: string;
  highlight?: string;
}) {
  const tx = useAppText();

  return (
    <div className="bg-white p-6 rounded-2xl shadow-sm border border-gray-100 flex flex-col justify-center">
      <div className="text-sm font-medium text-gray-400 uppercase tracking-wider mb-1">
        {tx(label)}
      </div>
      <div className={`text-2xl font-bold ${highlight}`}>{value}</div>
    </div>
  );
}
