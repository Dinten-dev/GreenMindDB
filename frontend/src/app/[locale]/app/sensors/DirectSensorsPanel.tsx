'use client';

import { useEffect, useState } from 'react';

type DirectDevice = {
  id: string;
  hardware_id: string | null;
  zone_id: string;
  status: 'online' | 'offline' | 'disabled';
  last_seen: string | null;
  spool_bytes: number;
};

export default function DirectSensorsPanel() {
  const [devices, setDevices] = useState<DirectDevice[]>([]);
  const [available, setAvailable] = useState(false);
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    const refresh = async () => {
      try {
        const response = await fetch('/api/v1/direct-ingest/devices', { cache: 'no-store' });
        if (!response.ok) throw new Error('Direct-Status nicht verfügbar');
        const data: DirectDevice[] = await response.json();
        if (active) {
          setDevices(data);
          setAvailable(true);
          setError(false);
        }
      } catch {
        if (active) setError(true);
      }
    };
    void refresh();
    const timer = setInterval(refresh, 10000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);
  if (!available) return null;
  return (
    <section
      className="rounded-2xl border border-emerald-100 bg-white p-6"
      aria-label="Direkt verbundene Sensoren"
    >
      <h2 className="text-lg font-semibold text-gray-900">Direkt über WLAN</h2>
      <p className="mt-1 text-sm text-gray-500">
        Der Empfang wird alle zehn Sekunden geprüft. Diese Testdaten werden separat von
        Gateway-Messungen gespeichert.
      </p>
      {error && (
        <p role="alert" className="mt-3 text-sm text-amber-800">
          Empfangsstatus konnte nicht aktualisiert werden. Die Anzeige kann veraltet sein.
        </p>
      )}
      {devices.length === 0 ? (
        <p className="mt-5 text-gray-600">
          Noch kein Sensor verbunden. Wähle beim Hinzufügen „Direkt über WLAN“.
        </p>
      ) : (
        <ul className="mt-4 divide-y divide-gray-100">
          {devices.map((device) => (
            <li key={device.id} className="flex flex-wrap items-center justify-between gap-3 py-4">
              <div>
                <h3 className="font-medium text-gray-900">
                  Biolingo {device.hardware_id?.slice(-5).replace(':', '') || device.id.slice(0, 8)}
                </h3>
                <p className="text-sm text-gray-500">
                  {device.last_seen
                    ? `Letzte Daten: ${new Date(device.last_seen).toLocaleString('de-CH')}`
                    : 'Verbunden – wartet auf die ersten Messdaten'}
                </p>
              </div>
              <span
                className={`rounded-full px-3 py-1 text-sm ${device.status === 'online' && !error ? 'bg-emerald-50 text-emerald-800' : 'bg-gray-100 text-gray-600'}`}
              >
                {error
                  ? 'Status unklar'
                  : device.status === 'online'
                    ? 'Empfängt Daten'
                    : device.status === 'disabled'
                      ? 'Deaktiviert'
                      : 'Keine aktuellen Daten'}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
