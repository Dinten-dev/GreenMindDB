// GreenMindDB Mac mini Dashboard API

const isServer = typeof window === 'undefined';

function getApiUrl(): string {
    if (isServer) {
        return process.env.INTERNAL_API_URL || 'http://backend:8000';
    }
    const explicitClientUrl = process.env.NEXT_PUBLIC_API_URL;
    if (explicitClientUrl) return explicitClientUrl;
    const protocol = window.location.protocol;
    const host = window.location.hostname;
    return `${protocol}//${host}:8000`;
}

const API_URL = getApiUrl();

// ── Types ────────────────────────────────────

export interface HealthStatus {
    status: 'ok' | 'degraded';
    db: 'ok' | 'error';
    minio: 'ok' | 'error';
}

export interface DashboardOverview {
    greenhouses: number;
    devices: number;
    sensors: number;
    plants: number;
    signal_rows_24h: number;
    env_rows_24h: number;
    ingests_24h: number;
}

export interface Device {
    id: string;
    serial: string;
    type: string;
    fw_version: string | null;
    last_seen: string | null;
    status: string;
    greenhouse_id: string | null;
    greenhouse_name: string | null;
    sensor_count: number;
}

export interface IngestLogEntry {
    request_id: string;
    received_at: string;
    endpoint: string;
    source: string | null;
    status: string;
    details: Record<string, unknown>;
}

export interface Greenhouse {
    id: string;
    name: string;
    location: string | null;
    timezone: string;
    zone_count: number;
    device_count: number;
    plant_count: number;
}

// ── API Functions ────────────────────────────

export async function fetchHealth(): Promise<HealthStatus> {
    const res = await fetch(`${API_URL}/health`, { cache: 'no-store' });
    if (!res.ok) throw new Error('Health check failed');
    return res.json();
}

export async function fetchOverview(): Promise<DashboardOverview> {
    const res = await fetch(`${API_URL}/operator/overview`, { cache: 'no-store' });
    if (!res.ok) throw new Error('Failed to fetch overview');
    return res.json();
}

export async function fetchDevices(): Promise<Device[]> {
    const res = await fetch(`${API_URL}/operator/devices`, { cache: 'no-store' });
    if (!res.ok) throw new Error('Failed to fetch devices');
    return res.json();
}

export async function fetchIngestLog(limit = 30): Promise<IngestLogEntry[]> {
    const res = await fetch(`${API_URL}/operator/ingest-log?limit=${limit}`, { cache: 'no-store' });
    if (!res.ok) throw new Error('Failed to fetch ingest log');
    return res.json();
}

export async function fetchGreenhouses(): Promise<Greenhouse[]> {
    const res = await fetch(`${API_URL}/operator/greenhouses`, { cache: 'no-store' });
    if (!res.ok) throw new Error('Failed to fetch greenhouses');
    return res.json();
}

export async function fetchGreenhouse(id: string): Promise<Greenhouse> {
    const res = await fetch(`${API_URL}/operator/greenhouses/${id}`, { cache: 'no-store' });
    if (!res.ok) throw new Error('Failed to fetch greenhouse');
    return res.json();
}

// ── Admin API ────────────────────────────────

export interface GreenhouseCreate {
    name: string;
    location?: string;
    timezone?: string;
}

export interface DeviceCreate {
    greenhouse_id: string;
    serial: string;
    type: string; // 'gateway', 'sensor_node', 'cam'
    fw_version?: string;
}

export interface DeviceKeyResponse {
    device_id: string;
    api_key: string;
    warning: string;
}

export interface GreenhouseSummary {
    greenhouse_id: string;
    name: string;
    device_count: number;
    plant_count: number;
    active_device_count: number;
    last_seen: string | null;
}

export async function createGreenhouse(data: GreenhouseCreate): Promise<Greenhouse> {
    const res = await fetch(`${API_URL}/admin/greenhouses`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
    if (!res.ok) throw new Error('Failed to create greenhouse');
    return res.json();
}

export async function createDevice(data: DeviceCreate): Promise<DeviceKeyResponse> {
    const res = await fetch(`${API_URL}/admin/devices`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
    });
    if (!res.ok) throw new Error('Failed to create device');
    return res.json();
}

export async function rotateDeviceKey(deviceId: string): Promise<DeviceKeyResponse> {
    const res = await fetch(`${API_URL}/admin/devices/${deviceId}/rotate-key`, {
        method: 'POST',
    });
    if (!res.ok) throw new Error('Failed to rotate device key');
    return res.json();
}

export async function fetchGreenhouseSummary(id: string): Promise<GreenhouseSummary> {
    const res = await fetch(`${API_URL}/admin/greenhouses/${id}/summary`, { cache: 'no-store' });
    if (!res.ok) throw new Error('Failed to fetch greenhouse summary');
    return res.json();
}

// ── Live Data & Monitoring ───────────────────

export interface DeviceLiveData {
    device_id: string;
    timestamp: string;
    sensors: Record<string, {
        kind: string;
        unit: string;
        value: number;
        time: string;
        quality: number;
    }>;
}

export async function fetchDeviceLive(deviceId: string): Promise<DeviceLiveData> {
    const res = await fetch(`${API_URL}/operator/devices/${deviceId}/live`, { cache: 'no-store' });
    if (!res.ok) throw new Error('Failed to fetch live data');
    return res.json();
}

export function getDeviceDownloadUrl(deviceId: string, metric: 'env' | 'signal', from?: Date, to?: Date): string {
    const params = new URLSearchParams({ metric });
    if (from) params.append('from', from.toISOString());
    if (to) params.append('to', to.toISOString());
    return `${API_URL}/operator/devices/${deviceId}/download?${params.toString()}`;
}
