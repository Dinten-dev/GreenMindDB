const API_BASE = typeof window === 'undefined'
    ? (process.env.INTERNAL_API_URL || 'http://localhost:8000')
    : '/api';

interface FetchOptions extends RequestInit {
    params?: Record<string, string>;
}

async function apiFetch<T>(path: string, options: FetchOptions = {}): Promise<T> {
    const { params, ...fetchOpts } = options;

    let url = `${API_BASE}${path}`;
    if (params) {
        const searchParams = new URLSearchParams(params);
        url += `?${searchParams.toString()}`;
    }

    const res = await fetch(url, {
        credentials: 'include',
        headers: {
            'Content-Type': 'application/json',
            ...fetchOpts.headers,
        },
        ...fetchOpts,
    });

    if (!res.ok) {
        const body = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(body.detail || `API error ${res.status}`);
    }

    if (res.status === 204) return {} as T;
    return res.json();
}

// ── Auth ─────────────────────────────────────
export interface AuthUser {
    id: string;
    email: string;
    name: string | null;
    role: string;
    organization_id: string | null;
    organization_name: string | null;
    is_active: boolean;
}

interface TokenResponse {
    access_token: string;
    token_type: string;
    user: AuthUser;
}

export async function apiSignup(email: string, password: string, name: string): Promise<TokenResponse> {
    return apiFetch<TokenResponse>('/auth/signup', {
        method: 'POST',
        body: JSON.stringify({ email, password, name }),
    });
}

export async function apiLogin(email: string, password: string): Promise<TokenResponse> {
    return apiFetch<TokenResponse>('/auth/login', {
        method: 'POST',
        body: JSON.stringify({ email, password }),
    });
}

export async function apiLogout(): Promise<void> {
    await apiFetch('/auth/logout', { method: 'POST' });
}

export async function apiGetMe(): Promise<AuthUser> {
    return apiFetch<AuthUser>('/auth/me');
}

// ── Organizations ────────────────────────────
export interface Org {
    id: string;
    name: string;
    created_at: string;
}

export async function apiGetOrg(): Promise<Org | null> {
    return apiFetch<Org | null>('/organizations');
}

export async function apiCreateOrg(name: string): Promise<Org> {
    return apiFetch<Org>('/organizations', {
        method: 'POST',
        body: JSON.stringify({ name }),
    });
}

// ── Greenhouses ──────────────────────────────
export interface Greenhouse {
    id: string;
    name: string;
    location: string | null;
    created_at: string;
    device_count: number;
    sensor_count: number;
}

export interface GreenhouseOverview {
    id: string;
    name: string;
    total_devices: number;
    online_devices: number;
    total_sensors: number;
    readings_24h: number;
}

export async function apiListGreenhouses(): Promise<Greenhouse[]> {
    return apiFetch<Greenhouse[]>('/greenhouses');
}

export async function apiCreateGreenhouse(name: string, location?: string): Promise<Greenhouse> {
    return apiFetch<Greenhouse>('/greenhouses', {
        method: 'POST',
        body: JSON.stringify({ name, location }),
    });
}

export async function apiGetGreenhouseOverview(id: string): Promise<GreenhouseOverview> {
    return apiFetch<GreenhouseOverview>(`/greenhouses/${id}/overview`);
}

// ── Devices ──────────────────────────────────
export interface DeviceInfo {
    id: string;
    serial: string;
    name: string | null;
    type: string;
    fw_version: string | null;
    status: string;
    last_seen: string | null;
    greenhouse_id: string | null;
    greenhouse_name: string | null;
    sensor_count: number;
    paired_at: string | null;
}

export interface PairingCode {
    code: string;
    expires_at: string;
    greenhouse_id: string;
}

export async function apiListDevices(): Promise<DeviceInfo[]> {
    return apiFetch<DeviceInfo[]>('/devices');
}

export async function apiGeneratePairingCode(greenhouse_id: string): Promise<PairingCode> {
    return apiFetch<PairingCode>('/devices/pairing-code', {
        method: 'POST',
        body: JSON.stringify({ greenhouse_id }),
    });
}

// ── Sensors ──────────────────────────────────
export interface SensorInfo {
    id: string;
    device_id: string;
    kind: string;
    unit: string;
    label: string | null;
    device_serial: string | null;
    device_name: string | null;
    device_status: string | null;
}

export interface DataPoint {
    timestamp: string;
    value: number;
}

export interface SensorData {
    sensor_id: string;
    kind: string;
    unit: string;
    label: string | null;
    data: DataPoint[];
}

export async function apiListSensors(greenhouse_id?: string): Promise<SensorInfo[]> {
    const params = greenhouse_id ? { greenhouse_id } : undefined;
    return apiFetch<SensorInfo[]>('/sensors', { params });
}

export async function apiGetSensorData(sensor_id: string, range: string = '24h'): Promise<SensorData> {
    return apiFetch<SensorData>(`/sensors/${sensor_id}/data`, { params: { range } });
}
