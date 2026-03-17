'use client';

import { useState, useEffect } from 'react';
import { apiListDevices, apiListGreenhouses, apiGeneratePairingCode, DeviceInfo, Greenhouse, PairingCode } from '@/lib/api';

export default function DevicesPage() {
    const [devices, setDevices] = useState<DeviceInfo[]>([]);
    const [greenhouses, setGreenhouses] = useState<Greenhouse[]>([]);
    const [loading, setLoading] = useState(true);
    const [showPairing, setShowPairing] = useState(false);
    const [selectedGh, setSelectedGh] = useState('');
    const [pairingCode, setPairingCode] = useState<PairingCode | null>(null);
    const [generating, setGenerating] = useState(false);

    useEffect(() => {
        loadData();
    }, []);

    const loadData = async () => {
        try {
            const [dev, gh] = await Promise.all([apiListDevices(), apiListGreenhouses()]);
            setDevices(dev);
            setGreenhouses(gh);
            if (gh.length > 0) setSelectedGh(gh[0].id);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const handleGenerateCode = async () => {
        if (!selectedGh) return;
        setGenerating(true);
        try {
            const code = await apiGeneratePairingCode(selectedGh);
            setPairingCode(code);
        } catch (err) {
            console.error(err);
        } finally {
            setGenerating(false);
        }
    };

    if (loading) {
        return (
            <div className="animate-pulse space-y-4">
                <div className="h-8 w-32 bg-apple-gray-200 rounded-lg" />
                <div className="h-48 bg-apple-gray-200 rounded-apple-lg" />
            </div>
        );
    }

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-apple-gray-800">Devices</h1>
                    <p className="text-sm text-apple-gray-400 mt-1">Manage and pair your sensor nodes</p>
                </div>
                <button
                    onClick={() => { setShowPairing(true); setPairingCode(null); }}
                    disabled={greenhouses.length === 0}
                    className="px-4 py-2 bg-gm-green-500 text-white rounded-full text-sm font-medium hover:bg-gm-green-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                    + Pair Device
                </button>
            </div>

            {/* Pairing Modal */}
            {showPairing && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 backdrop-blur-sm">
                    <div className="bg-white rounded-apple-xl shadow-apple-lg p-8 w-full max-w-md mx-4">
                        <h2 className="text-xl font-semibold text-apple-gray-800 mb-6">Pair a Device</h2>

                        {!pairingCode ? (
                            <div className="space-y-4">
                                <div>
                                    <label className="block text-sm font-medium text-apple-gray-600 mb-1.5">Greenhouse</label>
                                    <select
                                        value={selectedGh}
                                        onChange={(e) => setSelectedGh(e.target.value)}
                                        className="w-full px-4 py-2.5 rounded-apple bg-apple-gray-100 border border-apple-gray-200 text-sm text-apple-gray-800 focus:outline-none focus:ring-2 focus:ring-gm-green-500"
                                    >
                                        {greenhouses.map(gh => (
                                            <option key={gh.id} value={gh.id}>{gh.name}</option>
                                        ))}
                                    </select>
                                </div>
                                <div className="flex gap-3 pt-2">
                                    <button
                                        onClick={() => setShowPairing(false)}
                                        className="flex-1 py-2.5 bg-apple-gray-100 text-apple-gray-600 rounded-apple text-sm font-medium hover:bg-apple-gray-200 transition-colors"
                                    >
                                        Cancel
                                    </button>
                                    <button
                                        onClick={handleGenerateCode}
                                        disabled={generating}
                                        className="flex-1 py-2.5 bg-gm-green-500 text-white rounded-apple text-sm font-medium hover:bg-gm-green-600 transition-colors disabled:opacity-50"
                                    >
                                        {generating ? 'Generating…' : 'Generate Code'}
                                    </button>
                                </div>
                            </div>
                        ) : (
                            <div className="text-center space-y-4">
                                <p className="text-sm text-apple-gray-400">Enter this code on your device:</p>
                                <div className="bg-apple-gray-100 rounded-apple-lg py-6 px-4">
                                    <p className="text-4xl font-mono font-bold tracking-[0.3em] text-apple-gray-800">
                                        {pairingCode.code}
                                    </p>
                                </div>
                                <p className="text-xs text-apple-gray-400">
                                    Expires at {new Date(pairingCode.expires_at).toLocaleTimeString()}
                                </p>
                                <button
                                    onClick={() => { setShowPairing(false); loadData(); }}
                                    className="w-full py-2.5 bg-apple-gray-100 text-apple-gray-600 rounded-apple text-sm font-medium hover:bg-apple-gray-200 transition-colors"
                                >
                                    Done
                                </button>
                            </div>
                        )}
                    </div>
                </div>
            )}

            {/* Device List */}
            {devices.length === 0 ? (
                <div className="bg-white rounded-apple-lg shadow-apple-card p-12 text-center">
                    <div className="text-4xl mb-4">📡</div>
                    <h3 className="text-lg font-semibold text-apple-gray-800 mb-2">No devices paired</h3>
                    <p className="text-sm text-apple-gray-400 mb-4">
                        {greenhouses.length === 0
                            ? 'Create a greenhouse first, then pair your sensor nodes.'
                            : 'Generate a pairing code and enter it on your ESP32 device.'}
                    </p>
                </div>
            ) : (
                <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
                    {devices.map(dev => (
                        <div key={dev.id} className="bg-white rounded-apple-lg shadow-apple-card p-5 hover:shadow-apple transition-shadow duration-300">
                            <div className="flex items-start justify-between mb-3">
                                <div>
                                    <p className="font-semibold text-apple-gray-800">{dev.name || dev.serial}</p>
                                    <p className="text-xs text-apple-gray-400 font-mono">{dev.serial}</p>
                                </div>
                                <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium ${dev.status === 'online'
                                        ? 'bg-gm-green-50 text-gm-green-600'
                                        : 'bg-apple-gray-100 text-apple-gray-400'
                                    }`}>
                                    <span className={`w-1.5 h-1.5 rounded-full ${dev.status === 'online' ? 'bg-gm-green-500' : 'bg-apple-gray-300'}`} />
                                    {dev.status}
                                </span>
                            </div>
                            <div className="grid grid-cols-2 gap-y-2 text-sm">
                                <span className="text-apple-gray-400">Type</span>
                                <span className="text-apple-gray-700">{dev.type}</span>
                                <span className="text-apple-gray-400">Sensors</span>
                                <span className="text-apple-gray-700">{dev.sensor_count}</span>
                                {dev.greenhouse_name && (
                                    <>
                                        <span className="text-apple-gray-400">Greenhouse</span>
                                        <span className="text-apple-gray-700">{dev.greenhouse_name}</span>
                                    </>
                                )}
                                {dev.fw_version && (
                                    <>
                                        <span className="text-apple-gray-400">Firmware</span>
                                        <span className="text-apple-gray-700 font-mono text-xs">{dev.fw_version}</span>
                                    </>
                                )}
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
}
