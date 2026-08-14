import { Plant, PlantSensorAssignment, ObservationAccess } from '@/types';
import { apiFetch } from '@/lib/api';

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

export async function apiDeletePlant(plantId: string): Promise<void> {
    return apiFetch<void>(`/plants/${plantId}`, {
        method: 'DELETE',
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
