'use client';

import { useAppText } from '@/i18n/app-text';

import { useState, useEffect, useCallback } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import QRCode from 'react-qr-code';
import { apiGetPlant, apiGetObservationAccess } from '@/lib/plants-api';
import { Plant, ObservationAccess } from '@/types';

export default function PrintQRPage() {
  const tx = useAppText();

  const params = useParams() as { plantId: string };
  const plantId = params.plantId;

  const [plant, setPlant] = useState<Plant | null>(null);
  const [access, setAccess] = useState<ObservationAccess | null>(null);
  const [loading, setLoading] = useState(true);
  const [accessError, setAccessError] = useState(false);
  const [baseUrl, setBaseUrl] = useState('');

  const loadData = useCallback(async () => {
    try {
      const p = await apiGetPlant(plantId);
      setPlant(p);

      // Printing only reads an existing grant.
      const a = await apiGetObservationAccess(plantId);
      setAccess(a);
      setAccessError(false);
    } catch (err) {
      setAccessError(true);
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, [plantId]);

  useEffect(() => {
    setBaseUrl(window.location.origin);
    loadData();
  }, [loadData]);

  if (loading) return <div className="p-8 text-gray-500">{tx('Lade QR Code...')}</div>;
  if (!plant) return <div className="p-8 text-red-500">{tx('Fehler beim Laden.')}</div>;
  if (accessError) {
    return (
      <div className="p-8 space-y-4" role="alert">
        <p>
          {tx(
            'Zugangsstatus konnte nicht geladen werden. Öffentliche Zugänge können weiterhin aktiv sein.'
          )}
        </p>
        <Link href={`/${tx.locale}/app/plants/${plant.id}`} className="underline">
          {tx('Zur Pflanze')}
        </Link>
      </div>
    );
  }
  if (!access?.is_active) {
    return (
      <div className="p-8 space-y-4">
        <p>
          {tx(
            'Kein aktiver öffentlicher Zugang. Stellen Sie ihn auf der Pflanzenseite bewusst neu aus.'
          )}
        </p>
        <Link href={`/${tx.locale}/app/plants/${plant.id}`} className="underline">
          {tx('Zur Pflanze')}
        </Link>
      </div>
    );
  }

  const observePath = `/${tx.locale}/observe/${access.public_id}`;
  const fullUrl = `${baseUrl}${observePath}`;

  return (
    <div className="space-y-6 max-w-2xl mx-auto">
      <div className="flex items-center gap-4 print:hidden">
        <Link
          href={`/${tx.locale}/app/plants/${plant.id}`}
          className="p-2 bg-white/40 rounded-xl hover:bg-white/60"
        >
          ←
        </Link>
        <div>
          <h1 className="text-2xl font-bold text-gray-800">
            {tx('QR Code für')} {plant.name}
          </h1>
        </div>
      </div>

      <div className="bg-white p-12 rounded-2xl shadow-sm border border-gray-100 flex flex-col items-center justify-center text-center">
        <h2 className="text-2xl font-bold text-gray-900 mb-2">{plant.name}</h2>
        {plant.plant_code && <p className="text-gray-500 mb-8">{plant.plant_code}</p>}

        <div className="p-4 border-2 border-emerald-500 rounded-2xl bg-white">
          <QRCode
            value={fullUrl}
            title={tx('QR Code für {value0}', { value0: plant.name })}
            level="M"
            size={256}
          />
        </div>

        <p className="mt-8 text-sm text-gray-600 max-w-sm">
          {tx('Scannen Sie diesen Code, um eine biologische Feldbewertung ohne Login abzugeben.')}
        </p>

        <div className="mt-8 pt-8 border-t border-gray-100 w-full print:hidden">
          <button
            onClick={() => window.print()}
            className="px-6 py-3 bg-gray-900 text-white rounded-full font-medium shadow-lg hover:bg-black transition-colors"
          >
            {tx('QR Code Drucken')}
          </button>
          <p className="mt-4 text-xs text-gray-400 font-mono break-all">{fullUrl}</p>
        </div>
      </div>

      <style jsx global>{`
        @media print {
          body * {
            visibility: hidden;
          }
          .bg-white,
          .bg-white * {
            visibility: visible;
          }
          .bg-white {
            position: absolute;
            left: 0;
            top: 0;
            width: 100%;
            border: none;
            box-shadow: none;
          }
        }
      `}</style>
    </div>
  );
}
