import { PlantObservation, PlantObservationPhoto, PublicPlantContext } from '@/types';

const API_BASE = typeof window === 'undefined'
    ? `${process.env.INTERNAL_API_URL || 'http://localhost:8000'}/api/v1`
    : '/api/v1';

async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
    const res = await fetch(`${API_BASE}${path}`, {
        ...options,
        headers: {
            // Note: Content-Type is omitted if FormData is used
            ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
            ...options.headers,
        },
    });

    if (!res.ok) {
        const body = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(body.detail || `API error ${res.status}`);
    }

    return res.json();
}

export interface ObservationSession {
    session_token: string;
    expires_at: string;
}

export async function apiCreateSession(publicId: string): Promise<ObservationSession> {
    return apiFetch<ObservationSession>('/public/observe/session', {
        method: 'POST',
        body: JSON.stringify({ public_id: publicId }),
    });
}

export async function apiGetPlantContext(sessionToken: string): Promise<PublicPlantContext> {
    return apiFetch<PublicPlantContext>(`/public/observe/session/${sessionToken}/context`);
}

export async function apiCreateObservation(sessionToken: string, payload: Partial<PlantObservation>): Promise<PlantObservation> {
    return apiFetch<PlantObservation>(`/public/observe/session/${sessionToken}/observations`, {
        method: 'POST',
        body: JSON.stringify(payload),
    });
}

export async function apiUploadObservationPhoto(
    sessionToken: string,
    observationId: string,
    photoFile: File
): Promise<PlantObservationPhoto> {
    const formData = new FormData();
    formData.append('file', photoFile);

    return apiFetch<PlantObservationPhoto>(
        `/public/observe/session/${sessionToken}/observations/${observationId}/photos`,
        {
            method: 'POST',
            body: formData,
        }
    );
}
