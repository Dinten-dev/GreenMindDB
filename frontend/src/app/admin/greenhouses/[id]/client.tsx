'use client';

import { useEffect, useState } from 'react';
import {
    fetchGreenhouseSummary,
    fetchDevices,
    createDevice,
    rotateDeviceKey,
    GreenhouseSummary,
    Device,
    DeviceCreate,
    DeviceKeyResponse
} from '@/lib/api';
import Link from 'next/link';

export default function AdminGreenhouseDetailClient({ id }: { id: string }) {
    const [summary, setSummary] = useState<GreenhouseSummary | null>(null);
    const [devices, setDevices] = useState<Device[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    // Device Creation State
    const [showCreateForm, setShowCreateForm] = useState(false);
    const [newDevice, setNewDevice] = useState<DeviceCreate>({
        greenhouse_id: id,
        serial: '',
        type: 'sensor_node',
        fw_version: '1.0.0'
    });
    const [createdKey, setCreatedKey] = useState<DeviceKeyResponse | null>(null);

    useEffect(() => {
        loadData();
    }, [id]);

    const loadData = async () => {
        setLoading(true);
        try {
            const [sum, devs] = await Promise.all([
                fetchGreenhouseSummary(id),
                fetchDevices()
            ]);
            const filteredDevs = devs.filter(d => d.greenhouse_id === id);
            setDevices(filteredDevs);
            setSummary(sum);
        } catch (err: any) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    const handleCreateDevice = async (e: React.FormEvent) => {
        e.preventDefault();
        try {
            const res = await createDevice(newDevice);
            setCreatedKey(res);
            setShowCreateForm(false);
            setNewDevice({ ...newDevice, serial: '' }); // Reset serial
            loadData();
        } catch (err: any) {
            alert('Failed to create device: ' + err.message);
        }
    };

    const handleRotateKey = async (deviceId: string, serial: string) => {
        if (!confirm(`Are you sure you want to rotate the API key for device ${serial}? The old key will stop working immediately.`)) return;
        try {
            const res = await rotateDeviceKey(deviceId);
            setCreatedKey(res);
        } catch (err: any) {
            alert('Failed to rotate key: ' + err.message);
        }
    };

    if (loading) return <div className="p-8 text-center">Loading...</div>;
    if (error) return <div className="p-8 text-center text-red-600">Error: {error}</div>;
    if (!summary) return <div className="p-8 text-center">Greenhouse not found</div>;

    return (
        <main className="max-w-7xl mx-auto px-6 py-8">
            <div className="flex items-center gap-4 mb-8">
                <Link href="/admin/greenhouses" className="text-sm text-gray-500 hover:text-green-600 transition-colors">
                    ← Back to List
                </Link>
            </div>

            {/* Header & Stats */}
            <div className="bg-white rounded-2xl shadow-sm border border-gray-200 p-8 mb-8">
                <h1 className="text-3xl font-bold text-gray-900 mb-6">{summary.name}</h1>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
                    <div>
                        <span className="block text-sm text-gray-500">Total Devices</span>
                        <span className="text-2xl font-bold text-gray-900">{summary.device_count}</span>
                    </div>
                    <div>
                        <span className="block text-sm text-gray-500">Active Devices</span>
                        <span className="text-2xl font-bold text-green-600">{summary.active_device_count}</span>
                    </div>
                    <div>
                        <span className="block text-sm text-gray-500">Plants</span>
                        <span className="text-2xl font-bold text-gray-900">{summary.plant_count}</span>
                    </div>
                    <div>
                        <span className="block text-sm text-gray-500">Last Seen</span>
                        <span className="text-sm font-medium text-gray-700">
                            {summary.last_seen ? new Date(summary.last_seen).toLocaleString() : 'Never'}
                        </span>
                    </div>
                </div>
            </div>

            {/* API Key Modal */}
            {createdKey && (
                <div className="fixed inset-0 bg-black/50 flex items-center justify-center p-4 z-50">
                    <div className="bg-white rounded-xl max-w-lg w-full p-6 shadow-2xl">
                        <h3 className="text-xl font-bold text-red-600 mb-2">⚠️ Save this API Key</h3>
                        <p className="text-gray-600 mb-4">
                            This key will only be shown once. If you lose it, you will need to rotate the key again.
                        </p>
                        <div className="bg-gray-100 p-4 rounded-lg font-mono text-lg break-all border border-gray-300 mb-6 select-all">
                            {createdKey.api_key}
                        </div>
                        <div className="flex justify-end">
                            <button
                                onClick={() => setCreatedKey(null)}
                                className="bg-blue-600 hover:bg-blue-700 text-white px-6 py-2 rounded-lg font-medium"
                            >
                                I have saved it
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Devices Section */}
            <div className="flex justify-between items-center mb-6">
                <h2 className="text-2xl font-bold text-gray-900">Devices</h2>
                <button
                    onClick={() => setShowCreateForm(!showCreateForm)}
                    className="bg-green-600 hover:bg-green-700 text-white px-4 py-2 rounded-lg font-medium transition-colors"
                >
                    {showCreateForm ? 'Cancel' : '+ Add Device'}
                </button>
            </div>

            {showCreateForm && (
                <div className="bg-white p-6 rounded-xl shadow-md border border-gray-100 mb-8 max-w-2xl bg-gray-50">
                    <h3 className="text-lg font-bold text-gray-900 mb-4">Register New Device</h3>
                    <form onSubmit={handleCreateDevice} className="space-y-4">
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div>
                                <label className="block text-sm font-medium text-gray-700 mb-1">Serial Number</label>
                                <input
                                    type="text"
                                    required
                                    placeholder="e.g. ESP32-001"
                                    value={newDevice.serial}
                                    onChange={e => setNewDevice({ ...newDevice, serial: e.target.value })}
                                    className="w-full border-gray-300 rounded-md shadow-sm focus:border-green-500 focus:ring-green-500"
                                />
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-gray-700 mb-1">Type</label>
                                <select
                                    value={newDevice.type}
                                    onChange={e => setNewDevice({ ...newDevice, type: e.target.value })}
                                    className="w-full border-gray-300 rounded-md shadow-sm focus:border-green-500 focus:ring-green-500"
                                >
                                    <option value="sensor_node">Sensor Node</option>
                                    <option value="gateway">Gateway</option>
                                    <option value="camera">Camera</option>
                                </select>
                            </div>
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-gray-700 mb-1">Firmware Version</label>
                            <input
                                type="text"
                                value={newDevice.fw_version || ''}
                                onChange={e => setNewDevice({ ...newDevice, fw_version: e.target.value })}
                                className="w-full border-gray-300 rounded-md shadow-sm focus:border-green-500 focus:ring-green-500"
                            />
                        </div>
                        <div className="flex justify-end pt-2">
                            <button
                                type="submit"
                                className="bg-green-600 hover:bg-green-700 text-white px-6 py-2 rounded-lg font-medium transition-colors"
                            >
                                Register Device
                            </button>
                        </div>
                    </form>
                </div>
            )}

            <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
                <table className="min-w-full divide-y divide-gray-200">
                    <thead className="bg-gray-50">
                        <tr>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Serial</th>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Type</th>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th>
                            <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Last Seen</th>
                            <th className="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Actions</th>
                        </tr>
                    </thead>
                    <tbody className="bg-white divide-y divide-gray-200">
                        {devices.map(device => (
                            <tr key={device.id} className="hover:bg-gray-50 transition-colors">
                                <td className="px-6 py-4 whitespace-nowrap font-medium text-gray-900">{device.serial}</td>
                                <td className="px-6 py-4 whitespace-nowrap text-gray-500 capitalize">{device.type.replace('_', ' ')}</td>
                                <td className="px-6 py-4 whitespace-nowrap">
                                    <span className={`px-2 py-1 inline-flex text-xs leading-5 font-semibold rounded-full ${device.status === 'online' ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-800'
                                        }`}>
                                        {device.status}
                                    </span>
                                </td>
                                <td className="px-6 py-4 whitespace-nowrap text-gray-500 text-sm">
                                    {device.last_seen ? new Date(device.last_seen).toLocaleString() : '-'}
                                </td>
                                <td className="px-6 py-4 whitespace-nowrap text-right text-sm font-medium">
                                    <button
                                        onClick={() => handleRotateKey(device.id, device.serial)}
                                        className="text-indigo-600 hover:text-indigo-900 mr-4"
                                    >
                                        Rotate Key
                                    </button>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
                {devices.length === 0 && (
                    <div className="text-center py-12 text-gray-500">No devices found.</div>
                )}
            </div>
        </main>
    );
}
