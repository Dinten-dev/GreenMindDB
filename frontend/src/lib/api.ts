// Auto-detect API URL based on current browser location
const isServer = typeof window === 'undefined';

function getApiUrl(): string {
    if (isServer) {
        // Server-side: use Docker internal network
        return process.env.INTERNAL_API_URL || 'http://backend:8000';
    }
    const explicitClientUrl = process.env.NEXT_PUBLIC_API_URL;
    if (explicitClientUrl) {
        return explicitClientUrl;
    }
    // Client-side: use same host as frontend, but port 8000
    const protocol = window.location.protocol;
    const host = window.location.hostname;
    return `${protocol}//${host}:8000`;
}

const API_URL = getApiUrl();

// === Helper for auth headers ===
function getAuthHeaders(): Record<string, string> {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (!isServer) {
        const token = localStorage.getItem('auth_token');
        if (token) {
            headers['Authorization'] = `Bearer ${token}`;
        }
    }
    return headers;
}

export interface User {
    id: number;
    email: string;
    is_active: boolean;
}

export interface Species {
    id: number;
    common_name: string;
    latin_name: string;
    category: string;
    notes: string | null;
}

export interface Metric {
    id: number;
    key: string;
    label: string;
    unit: string;
    description: string | null;
}

export interface Source {
    id: number;
    title: string;
    publisher: string;
    year: number | null;
    url: string | null;
    notes: string | null;
}

export interface TargetRange {
    id: number;
    species_id: number;
    metric_id: number;
    optimal_low: number;
    optimal_high: number;
    source_id: number;
    metric: Metric;
    source: Source;
}

export interface SpeciesDetail extends Species {
    target_ranges: TargetRange[];
}

export interface AuditEntry {
    id: number;
    timestamp: string;
    user_email: string | null;
    entity_type: string;
    action: string;
    diff_json: { before: Record<string, unknown> | null; after: Record<string, unknown> | null };
}

// === Source input for inline creation ===
export interface SourceInput {
    source_type: 'url' | 'own_experience';
    url?: string;
    title?: string;
    publisher?: string;
    year?: number;
    notes?: string;
}

// === AUTH Functions ===
export async function signup(email: string, password: string): Promise<User> {
    const res = await fetch(`${API_URL}/auth/signup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password })
    });
    if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Signup failed');
    }
    return res.json();
}

export async function login(email: string, password: string): Promise<string> {
    const res = await fetch(`${API_URL}/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password })
    });
    if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Login failed');
    }
    const data = await res.json();
    return data.access_token;
}

export async function getMe(): Promise<User> {
    const res = await fetch(`${API_URL}/auth/me`, {
        headers: getAuthHeaders()
    });
    if (!res.ok) throw new Error('Not authenticated');
    return res.json();
}

// === READ Functions ===
export async function fetchSpecies(): Promise<Species[]> {
    const res = await fetch(`${API_URL}/species`, { cache: 'no-store' });
    if (!res.ok) throw new Error('Failed to fetch species');
    return res.json();
}

export async function fetchSpeciesDetail(id: number): Promise<SpeciesDetail> {
    const res = await fetch(`${API_URL}/species/${id}`, { cache: 'no-store' });
    if (!res.ok) throw new Error('Failed to fetch species detail');
    return res.json();
}

export async function fetchSpeciesTargets(id: number): Promise<TargetRange[]> {
    const res = await fetch(`${API_URL}/species/${id}/targets`, { cache: 'no-store' });
    if (!res.ok) throw new Error('Failed to fetch targets');
    return res.json();
}

export async function fetchSpeciesHistory(id: number, limit = 50): Promise<AuditEntry[]> {
    const res = await fetch(`${API_URL}/species/${id}/history?limit=${limit}`, { cache: 'no-store' });
    if (!res.ok) throw new Error('Failed to fetch history');
    return res.json();
}

export async function fetchSources(): Promise<Source[]> {
    const res = await fetch(`${API_URL}/sources`, { cache: 'no-store' });
    if (!res.ok) throw new Error('Failed to fetch sources');
    return res.json();
}

export async function fetchMetrics(): Promise<Metric[]> {
    const res = await fetch(`${API_URL}/metrics`, { cache: 'no-store' });
    if (!res.ok) throw new Error('Failed to fetch metrics');
    return res.json();
}

// === SPECIES CRUD (requires auth) ===
export async function createSpecies(data: { common_name: string; latin_name?: string; category?: string; notes?: string }): Promise<Species> {
    const res = await fetch(`${API_URL}/species`, {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify(data)
    });
    if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to create species');
    }
    return res.json();
}

export async function updateSpecies(id: number, data: { common_name?: string; latin_name?: string; category?: string; notes?: string | null }): Promise<Species> {
    const res = await fetch(`${API_URL}/species/${id}`, {
        method: 'PUT',
        headers: getAuthHeaders(),
        body: JSON.stringify(data)
    });
    if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to update species');
    }
    return res.json();
}

export async function deleteSpecies(id: number): Promise<void> {
    const res = await fetch(`${API_URL}/species/${id}`, {
        method: 'DELETE',
        headers: getAuthHeaders()
    });
    if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to delete species');
    }
}

// === TARGET RANGE CRUD (with inline source) ===
export async function createTargetRange(data: {
    species_id: number;
    metric_id: number;
    optimal_low: number;
    optimal_high: number;
    source: SourceInput;
}): Promise<TargetRange> {
    const res = await fetch(`${API_URL}/target-ranges`, {
        method: 'POST',
        headers: getAuthHeaders(),
        body: JSON.stringify(data)
    });
    if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to create target range');
    }
    return res.json();
}

export async function updateTargetRange(id: number, data: {
    optimal_low?: number;
    optimal_high?: number;
    source?: SourceInput;
}): Promise<TargetRange> {
    const res = await fetch(`${API_URL}/target-ranges/${id}`, {
        method: 'PUT',
        headers: getAuthHeaders(),
        body: JSON.stringify(data)
    });
    if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to update target range');
    }
    return res.json();
}

export async function deleteTargetRange(id: number): Promise<void> {
    const res = await fetch(`${API_URL}/target-ranges/${id}`, {
        method: 'DELETE',
        headers: getAuthHeaders()
    });
    if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to delete target range');
    }
}

// Metric display order
export const METRIC_ORDER = [
    'air_temperature_c',
    'rel_humidity_pct',
    'soil_moisture_vwc_pct',
    'light_ppfd_umol_m2_s',
    'soil_ph'
];
