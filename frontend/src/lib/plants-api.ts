import { Plant, PlantSensorAssignment, ObservationAccess } from '@/types';

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

export async function apiListPlants(zone_id?: string): Promise<Plant[]> {
    const params = zone_id ? { zone_id } : undefined;
    return apiFetch<Plant[]>('/plants', { params });
}

export async function apiGetPlant(plantId: string): Promise<Plant> {
    return apiFetch<Plant>(`/plants/${plantId}`);
}

export async function apiCreatePlant(payload: Partial<Plant>): Promise<Plant> {
    return apiFetch<Plant>('/plants', {
        method: 'POST',
        body: JSON.stringify(payload),
    });
}

export async function apiUpdatePlant(plantId: string, payload: Partial<Plant>): Promise<Plant> {
    return apiFetch<Plant>(`/plants/${plantId}`, {
        method: 'PATCH',
        body: JSON.stringify(payload),
    });
}

export async function apiAssignSensor(plantId: string, sensorId: string, notes?: string): Promise<PlantSensorAssignment> {
    return apiFetch<PlantSensorAssignment>(`/plants/${plantId}/assign-sensor`, {
        method: 'POST',
        body: JSON.stringify({ sensor_id: sensorId, notes }),
    });
}

export async function apiGetSensorHistory(plantId: string): Promise<PlantSensorAssignment[]> {
    return apiFetch<PlantSensorAssignment[]>(`/plants/${plantId}/sensor-history`);
}

export async function apiCreateObservationAccess(plantId: string): Promise<ObservationAccess> {
    return apiFetch<ObservationAccess>(`/plants/${plantId}/observation-access`, {
        method: 'POST',
    });
}

export async function apiRevokeObservationAccess(plantId: string): Promise<ObservationAccess> {
    return apiFetch<ObservationAccess>(`/plants/${plantId}/observation-access`, {
        method: 'DELETE',
    });
}
