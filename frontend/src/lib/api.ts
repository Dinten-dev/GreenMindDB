const API_BASE = typeof window === 'undefined'
    ? `${process.env.INTERNAL_API_URL || 'http://localhost:8000'}/api/v1`
    : '/api/v1';

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

interface AuthResponse {
    detail: string;
    user: AuthUser;
}

export async function apiSignup(email: string, password: string, name: string): Promise<AuthResponse> {
    return apiFetch<AuthResponse>('/auth/signup', {
        method: 'POST',
        body: JSON.stringify({ email, password, name }),
    });
}

export async function apiLogin(email: string, password: string): Promise<AuthResponse> {
    return apiFetch<AuthResponse>('/auth/login', {
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
    gateway_count: number;
    sensor_count: number;
}

export interface GreenhouseOverview {
    id: string;
    name: string;
    total_gateways: number;
    online_gateways: number;
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

// ── Gateways ─────────────────────────────────
export interface GatewayInfo {
    id: string;
    greenhouse_id: string;
    greenhouse_name: string | null;
    hardware_id: string;
    name: string | null;
    local_ip: string | null;
    fw_version: string | null;
    status: string;
    is_active: boolean;
    last_seen: string | null;
    paired_at: string | null;
    sensor_count: number;
}

export interface PairingCode {
    code: string;
    expires_at: string;
    greenhouse_id: string;
}

export async function apiListGateways(greenhouse_id?: string): Promise<GatewayInfo[]> {
    const params = greenhouse_id ? { greenhouse_id } : undefined;
    return apiFetch<GatewayInfo[]>('/gateways', { params });
}

export async function apiDeleteGateway(gatewayId: string): Promise<void> {
    return apiFetch<void>(`/gateways/${gatewayId}`, { method: 'DELETE' });
}

export async function apiGeneratePairingCode(greenhouse_id: string): Promise<PairingCode> {
    return apiFetch<PairingCode>('/gateways/pairing-code', {
        method: 'POST',
        body: JSON.stringify({ greenhouse_id }),
    });
}

// ── Sensors (ESP32) ──────────────────────────
export interface SensorInfo {
    id: string;
    gateway_id: string;
    greenhouse_id: string | null;
    mac_address: string;
    name: string | null;
    sensor_type: string;
    status: string;
    last_seen: string | null;
    claimed_at: string | null;
    gateway_name: string | null;
    gateway_hardware_id: string | null;
}

export async function apiListSensors(greenhouse_id?: string, gateway_id?: string): Promise<SensorInfo[]> {
    const params: Record<string, string> = {};
    if (greenhouse_id) params.greenhouse_id = greenhouse_id;
    if (gateway_id) params.gateway_id = gateway_id;
    return apiFetch<SensorInfo[]>('/sensors', { params: Object.keys(params).length ? params : undefined });
}

export interface DataPoint {
    timestamp: string;
    value: number;
}

export interface SensorDataResponse {
    sensor_id: string;
    kind: string;
    unit: string;
    data: DataPoint[];
}

export async function apiGetSensorData(sensorId: string, range: string = '24h'): Promise<SensorDataResponse[]> {
    return apiFetch<SensorDataResponse[]>(`/sensors/${sensorId}/data`, { params: { range } });
}

export async function apiDeleteSensor(sensorId: string): Promise<void> {
    return apiFetch<void>(`/sensors/${sensorId}`, { method: 'DELETE' });
}

export async function apiGetSensorDataAdvanced(
    sensorId: string,
    opts: { range?: string; resolution?: string; date?: string }
): Promise<SensorDataResponse[]> {
    const params: Record<string, string> = {};
    if (opts.range) params.range = opts.range;
    if (opts.resolution) params.resolution = opts.resolution;
    if (opts.date) params.date = opts.date;
    return apiFetch<SensorDataResponse[]>(`/sensors/${sensorId}/data`, { params });
}

// ── Contact & Early Access ───────────────────
export interface ContactPayload {
    name: string;
    email: string;
    company?: string;
    message: string;
    website?: string;
}

export async function apiSubmitContact(payload: ContactPayload): Promise<{status: string}> {
    return apiFetch<{status: string}>('/contact', {
        method: 'POST',
        body: JSON.stringify({ company: '', website: '', ...payload }),
    });
}

export interface EarlyAccessPayload {
    name: string;
    company: string;
    email: string;
    country: string;
    message?: string;
    website?: string;
}

export async function apiSubmitEarlyAccess(payload: EarlyAccessPayload): Promise<{status: string}> {
    return apiFetch<{status: string}>('/early-access', {
        method: 'POST',
        body: JSON.stringify({ message: '', website: '', ...payload }),
    });
}

// ── Sensor Data Export ───────────────────────────
export async function apiExportSensorData(sensorId: string, range: string = '24h'): Promise<void> {
    const url = `${API_BASE}/sensors/${sensorId}/export?range=${range}`;
    const res = await fetch(url, {
        credentials: 'include',
    });

    if (!res.ok) {
        const body = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(body.detail || `Export failed: ${res.status}`);
    }

    const blob = await res.blob();
    const downloadUrl = URL.createObjectURL(blob);

    const disposition = res.headers.get('Content-Disposition') || '';
    const filenameMatch = disposition.match(/filename="?([^"]+)"?/);
    const filename = filenameMatch ? filenameMatch[1] : `sensor_export_${range}.zip`;

    const a = document.createElement('a');
    a.href = downloadUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();

    document.body.removeChild(a);
    URL.revokeObjectURL(downloadUrl);
}
