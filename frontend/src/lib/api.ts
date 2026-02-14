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
    const res = await fetch(`${API_URL}/v1/dashboard/overview`, { cache: 'no-store' });
    if (!res.ok) throw new Error('Failed to fetch overview');
    return res.json();
}

export async function fetchDevices(): Promise<Device[]> {
    const res = await fetch(`${API_URL}/v1/dashboard/devices`, { cache: 'no-store' });
    if (!res.ok) throw new Error('Failed to fetch devices');
    return res.json();
}

export async function fetchIngestLog(limit = 30): Promise<IngestLogEntry[]> {
    const res = await fetch(`${API_URL}/v1/dashboard/ingest-log?limit=${limit}`, { cache: 'no-store' });
    if (!res.ok) throw new Error('Failed to fetch ingest log');
    return res.json();
}

export async function fetchGreenhouses(): Promise<Greenhouse[]> {
    const res = await fetch(`${API_URL}/v1/dashboard/greenhouses`, { cache: 'no-store' });
    if (!res.ok) throw new Error('Failed to fetch greenhouses');
    return res.json();
}
