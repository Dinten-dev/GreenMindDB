/**
 * Firmware Admin API client.
 * Type-safe wrappers for all OTA management endpoints.
 */

const API_BASE =
    typeof window === 'undefined'
        ? `${process.env.INTERNAL_API_URL || 'http://localhost:8000'}/api/v1`
        : '/api/v1';

async function fwFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
    const res = await fetch(`${API_BASE}/firmware${path}`, {
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', ...options.headers },
        ...options,
    });
    if (!res.ok) {
        const body = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(body.detail || `API error ${res.status}`);
    }
    if (res.status === 204) return {} as T;
    return res.json();
}

// ── Types ────────────────────────────────────────────────────────────

export interface PaginationMeta {
    total: number;
    offset: number;
    limit: number;
}

export interface FirmwareRelease {
    id: string;
    version: string;
    board_type: string;
    hardware_revision: string;
    file_path: string;
    sha256: string;
    is_active: boolean;
    mandatory: boolean;
    min_version: string | null;
    changelog: string | null;
    created_at: string;
}

export interface FirmwareReleaseList {
    items: FirmwareRelease[];
    meta: PaginationMeta;
}

export interface DashboardSummary {
    active_releases: number;
    total_releases: number;
    online_gateways: number;
    total_gateways: number;
    total_devices: number;
    failed_updates_24h: number;
    successful_updates_24h: number;
    active_rollouts: number;
}

export interface FirmwareReport {
    id: string;
    sensor_id: string | null;
    gateway_id: string;
    release_id: string;
    status: string;
    error_message: string | null;
    reported_at: string;
    release_version: string | null;
    gateway_name: string | null;
}

export interface FirmwareReportList {
    items: FirmwareReport[];
    meta: PaginationMeta;
}

export interface RolloutPolicy {
    id: string;
    release_id: string;
    zone_id: string | null;
    canary_percentage: string;
    created_at: string;
    release_version: string | null;
    zone_name: string | null;
}

export interface AuditLogEntry {
    id: string;
    user_id: string | null;
    user_email: string | null;
    action: string;
    entity_type: string;
    entity_id: string | null;
    details: string | null;
    ip_address: string | null;
    created_at: string;
}

export interface AuditLogList {
    items: AuditLogEntry[];
    meta: PaginationMeta;
}

// ── API Functions ───────────────────────────────────────────────────

export async function getDashboard(): Promise<DashboardSummary> {
    return fwFetch<DashboardSummary>('/dashboard');
}

export async function listReleases(params?: {
    board_type?: string;
    search?: string;
    is_active?: boolean;
    offset?: number;
    limit?: number;
}): Promise<FirmwareReleaseList> {
    const searchParams = new URLSearchParams();
    if (params?.board_type) searchParams.set('board_type', params.board_type);
    if (params?.search) searchParams.set('search', params.search);
    if (params?.is_active !== undefined) searchParams.set('is_active', String(params.is_active));
    if (params?.offset !== undefined) searchParams.set('offset', String(params.offset));
    if (params?.limit !== undefined) searchParams.set('limit', String(params.limit));
    const qs = searchParams.toString();
    return fwFetch<FirmwareReleaseList>(`/releases${qs ? `?${qs}` : ''}`);
}

export async function uploadFirmware(formData: FormData): Promise<FirmwareRelease> {
    const res = await fetch(`${API_BASE}/firmware/upload`, {
        method: 'POST',
        credentials: 'include',
        body: formData,
        // No Content-Type header — browser sets multipart boundary
    });
    if (!res.ok) {
        const body = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(body.detail || `Upload failed: ${res.status}`);
    }
    return res.json();
}

export async function toggleRelease(releaseId: string, isActive: boolean): Promise<FirmwareRelease> {
    return fwFetch<FirmwareRelease>(`/releases/${releaseId}/status?is_active=${isActive}`, {
        method: 'PATCH',
    });
}

export async function deleteRelease(releaseId: string): Promise<void> {
    return fwFetch<void>(`/releases/${releaseId}`, { method: 'DELETE' });
}

export async function listReports(params?: {
    status?: string;
    gateway_id?: string;
    offset?: number;
    limit?: number;
}): Promise<FirmwareReportList> {
    const searchParams = new URLSearchParams();
    if (params?.status) searchParams.set('status', params.status);
    if (params?.gateway_id) searchParams.set('gateway_id', params.gateway_id);
    if (params?.offset !== undefined) searchParams.set('offset', String(params.offset));
    if (params?.limit !== undefined) searchParams.set('limit', String(params.limit));
    const qs = searchParams.toString();
    return fwFetch<FirmwareReportList>(`/reports${qs ? `?${qs}` : ''}`);
}

export async function listPolicies(): Promise<RolloutPolicy[]> {
    return fwFetch<RolloutPolicy[]>('/policies');
}

export async function createPolicy(data: {
    release_id: string;
    zone_id?: string | null;
    canary_percentage?: string;
}): Promise<RolloutPolicy> {
    return fwFetch<RolloutPolicy>('/policies', {
        method: 'POST',
        body: JSON.stringify(data),
    });
}

export async function deletePolicy(policyId: string): Promise<void> {
    return fwFetch<void>(`/policies/${policyId}`, { method: 'DELETE' });
}

export async function listAuditLogs(params?: {
    action?: string;
    offset?: number;
    limit?: number;
}): Promise<AuditLogList> {
    const searchParams = new URLSearchParams();
    if (params?.action) searchParams.set('action', params.action);
    if (params?.offset !== undefined) searchParams.set('offset', String(params.offset));
    if (params?.limit !== undefined) searchParams.set('limit', String(params.limit));
    const qs = searchParams.toString();
    return fwFetch<AuditLogList>(`/audit-logs${qs ? `?${qs}` : ''}`);
}
