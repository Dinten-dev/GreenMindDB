'use client';

import { useState, useEffect } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import Image from 'next/image';
import { apiGetPlant, apiCreateObservationAccess } from '@/lib/plants-api';
import { Plant, ObservationAccess } from '@/types';

export default function PrintQRPage() {
    const params = useParams() as { plantId: string };
    const plantId = params.plantId;

    const [plant, setPlant] = useState<Plant | null>(null);
    const [access, setAccess] = useState<ObservationAccess | null>(null);
    const [loading, setLoading] = useState(true);
    const [baseUrl, setBaseUrl] = useState('');

    useEffect(() => {
        setBaseUrl(window.location.origin);
        loadData();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [plantId]);

    const loadData = async () => {
        try {
            const p = await apiGetPlant(plantId);
            setPlant(p);
            
            // Get or create access
            const a = await apiCreateObservationAccess(plantId);
            setAccess(a);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    if (loading) return <div className="p-8 text-gray-500">Lade QR Code...</div>;
    if (!plant || !access) return <div className="p-8 text-red-500">Fehler beim Laden.</div>;

    const observePath = `/observe/${access.public_id}`;
    const fullUrl = `${baseUrl}${observePath}`;
    
    // Using a reliable public QR API since we can't install specific npm packages easily.
    const qrUrl = `https://api.qrserver.com/v1/create-qr-code/?size=400x400&data=${encodeURIComponent(fullUrl)}&margin=10`;

    return (
        <div className="space-y-6 max-w-2xl mx-auto">
            <div className="flex items-center gap-4 print:hidden">
                <Link href={`/app/plants/${plant.id}`} className="p-2 bg-white/40 rounded-xl hover:bg-white/60">
                    ←
                </Link>
                <div>
                    <h1 className="text-2xl font-bold text-gray-800">QR Code für {plant.name}</h1>
                </div>
            </div>

            <div className="bg-white p-12 rounded-2xl shadow-sm border border-gray-100 flex flex-col items-center justify-center text-center">
                <h2 className="text-2xl font-bold text-gray-900 mb-2">{plant.name}</h2>
                {plant.plant_code && <p className="text-gray-500 mb-8">{plant.plant_code}</p>}
                
                <div className="p-4 border-2 border-emerald-500 rounded-2xl bg-white">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img 
                        src={qrUrl} 
                        alt={`QR Code für ${plant.name}`} 
                        className="w-64 h-64"
                    />
                </div>
                
                <p className="mt-8 text-sm text-gray-600 max-w-sm">
                    Scannen Sie diesen Code, um eine biologische Feldbewertung ohne Login abzugeben.
                </p>
                
                <div className="mt-8 pt-8 border-t border-gray-100 w-full print:hidden">
                    <button 
                        onClick={() => window.print()}
                        className="px-6 py-3 bg-gray-900 text-white rounded-full font-medium shadow-lg hover:bg-black transition-colors"
                    >
                        QR Code Drucken
                    </button>
                    <p className="mt-4 text-xs text-gray-400 font-mono break-all">{fullUrl}</p>
                </div>
            </div>
            
            <style jsx global>{`
                @media print {
                    body * {
                        visibility: hidden;
                    }
                    .bg-white, .bg-white * {
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
