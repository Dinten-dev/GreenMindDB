'use client';

import { useAppText } from '@/i18n/app-text';
import { useEffect, useState } from 'react';
import { adminStorage, type StorageStatus, type StorageVolume } from '@/lib/administration-api';

const bytes = (value: number, locale: string) =>
  (value / 1_000_000_000).toLocaleString(locale, { maximumFractionDigits: 1 }) + ' GB';

function Volume({ title, value }: { title: string; value: StorageVolume }) {
  const tx = useAppText();

  return (
    <div className="space-y-3">
      <h3 className="font-semibold text-gray-800">{title}</h3>
      {value.warning && (
        <div
          role="alert"
          className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900"
        >
          <strong>
            {title}
            {tx(': Speicher wird knapp.')}
          </strong>{' '}
          {tx('Weniger als')} {value.warning_threshold_percent} {tx('% frei.')}
        </div>
      )}
      <div className="flex flex-wrap justify-between gap-3">
        <p className="text-2xl font-semibold text-gray-800">
          {value.used_percent.toLocaleString(tx.locale, { maximumFractionDigits: 1 })}{' '}
          {tx('% belegt')}
        </p>
        <p className="text-sm text-gray-600">
          {bytes(value.available_bytes, tx.locale)} {tx('von')}{' '}
          {bytes(value.total_bytes, tx.locale)} {tx('frei')}
        </p>
      </div>
      <div
        role="meter"
        aria-label={tx('Belegter Speicher: {value0}', { value0: title })}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={value.used_percent}
        className="h-3 overflow-hidden rounded-full bg-gray-100"
      >
        <div
          className={`h-full rounded-full ${value.warning ? 'bg-amber-500' : 'bg-emerald-600'}`}
          style={{ width: `${value.used_percent}%` }}
        />
      </div>
      <p className="text-xs text-gray-500">
        {tx('Für Dienste verfügbar:')}{' '}
        {value.free_percent.toLocaleString(tx.locale, { maximumFractionDigits: 2 })} %.
      </p>
    </div>
  );
}

export default function StorageMonitor() {
  const tx = useAppText();

  const [data, setData] = useState<StorageStatus | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const next = await adminStorage();
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
        if (active) timer = setTimeout(refresh, 60000);
      }
    };
    void refresh();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, []);
  return (
    <section className="glass-card p-6 space-y-4" aria-labelledby="storage-title">
      <h2 id="storage-title" className="text-lg font-semibold text-gray-800">
        {tx('Serverspeicher')}
      </h2>
      {error ? (
        <p role="status" className="text-sm text-gray-600">
          {tx(
            'Speicherstatus momentan nicht verfügbar. Die Anzeige wird automatisch erneut geladen.'
          )}
        </p>
      ) : !data ? (
        <p role="status" className="text-sm text-gray-500">
          {tx('Speicherstatus wird geladen …')}
        </p>
      ) : (
        <>
          <Volume title={tx('Systemlaufwerk')} value={data} />
          {data.wav_storage ? (
            <Volume title={tx('WAV-Datenlaufwerk')} value={data.wav_storage} />
          ) : (
            <p role="status" className="text-sm text-gray-600">
              {tx('WAV-Datenlaufwerk')}{' '}
              {data.wav_storage_configured
                ? tx('momentan nicht verfügbar.')
                : tx('noch nicht zur Überwachung eingerichtet.')}
            </p>
          )}
          <p className="text-xs text-gray-500">
            {tx('Stand:')}
            {new Date(data.checked_at).toLocaleString(tx.locale)}
            {tx('. Aktualisierung jede Minute. GB sind dezimale Gigabyte.')}
          </p>
        </>
      )}
    </section>
  );
}
