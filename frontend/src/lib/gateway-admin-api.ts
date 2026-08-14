/**
 * Gateway Remote Management API client.
 * Type-safe wrappers for RPi gateway fleet, releases, config, commands, and rollout.
 */

const API_BASE =
    typeof window === 'undefined'
        ? `${process.env.INTERNAL_API_URL || 'http://localhost:8000'}/api/v1`
        : '/api/v1';

async function gwFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
    const res = await fetch(`${API_BASE}/admin${path}`, {
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

export interface GatewayFleetItem {
    id: string;
    hardware_id: string;
    name: string | null;
    zone_name: string | null;
    status: string;
    app_version: string | null;
    config_version: string | null;
    agent_version: string | null;
    rollout_ring: string | null;
    maintenance_mode: boolean;
    blocked: boolean;
    disk_free_mb: number | null;
    disk_status: string | null;
    update_download_status: string | null;
    update_apply_status: string | null;
    signature_status: string | null;
    update_window: string | null;
    last_seen: string | null;
    desired_app_version: string | null;
    desired_config_version: string | null;
}

export interface GatewayFleetResponse {
    items: GatewayFleetItem[];
    total: number;
}

export interface GatewayAppRelease {
    id: string;
    version: string;
    sha256: string;
    signature: string | null;
    mandatory: boolean;
    is_active: boolean;
    channel: string;
    min_version: string | null;
    file_size_bytes: number | null;
    changelog: string | null;
    created_at: string;
}

export interface GatewayAppReleaseList {
    items: GatewayAppRelease[];
    total: number;
}

export interface GatewayConfigRelease {
    id: string;
    version: string;
    config_payload: Record<string, unknown>;
    schema_version: string;
    compatible_app_min: string | null;
    compatible_app_max: string | null;
    sha256: string;
    is_active: boolean;
    created_at: string;
}

export interface GatewayConfigReleaseList {
    items: GatewayConfigRelease[];
    total: number;
}

export interface GatewayCommand {
    id: string;
    gateway_id: string;
    command_type: string;
    payload: Record<string, unknown> | null;
    status: string;
    created_at: string;
    expires_at: string;
    delivered_at: string | null;
    executed_at: string | null;
    result_message: string | null;
}

export interface GatewayCommandList {
    items: GatewayCommand[];
    total: number;
}

export interface GatewayUpdateLog {
    id: string;
    gateway_id: string;
    gateway_name: string | null;
    update_type: string;
    from_version: string | null;
    to_version: string;
    status: string;
    error_message: string | null;
    started_at: string;
    completed_at: string | null;
}

export interface GatewayUpdateLogList {
    items: GatewayUpdateLog[];
    total: number;
}

// ── Fleet ───────────────────────────────────────────────────────────

export async function getFleetOverview(params?: {
    zone_id?: string;
    status?: string;
    offset?: number;
    limit?: number;
}): Promise<GatewayFleetResponse> {
    const sp = new URLSearchParams();
    if (params?.zone_id) sp.set('zone_id', params.zone_id);
    if (params?.status) sp.set('status', params.status);
    if (params?.offset !== undefined) sp.set('offset', String(params.offset));
    if (params?.limit !== undefined) sp.set('limit', String(params.limit));
    const qs = sp.toString();
    return gwFetch<GatewayFleetResponse>(`/gateway-fleet${qs ? `?${qs}` : ''}`);
}

// ── App Releases ────────────────────────────────────────────────────

export async function listAppReleases(params?: {
    channel?: string;
    is_active?: boolean;
    offset?: number;
    limit?: number;
}): Promise<GatewayAppReleaseList> {
    const sp = new URLSearchParams();
    if (params?.channel) sp.set('channel', params.channel);
    if (params?.is_active !== undefined) sp.set('is_active', String(params.is_active));
    if (params?.offset !== undefined) sp.set('offset', String(params.offset));
    if (params?.limit !== undefined) sp.set('limit', String(params.limit));
    const qs = sp.toString();
    return gwFetch<GatewayAppReleaseList>(`/gateway-app-releases${qs ? `?${qs}` : ''}`);
}

export async function uploadAppRelease(formData: FormData): Promise<GatewayAppRelease> {
    const res = await fetch(`${API_BASE}/admin/gateway-app-releases`, {
        method: 'POST',
        credentials: 'include',
        body: formData,
    });
    if (!res.ok) {
        const body = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(body.detail || `Upload failed: ${res.status}`);
    }
    return res.json();
}

export async function toggleAppRelease(id: string, isActive: boolean): Promise<GatewayAppRelease> {
    return gwFetch<GatewayAppRelease>(`/gateway-app-releases/${id}/status?is_active=${isActive}`, {
        method: 'PATCH',
    });
}

export async function deleteAppRelease(id: string): Promise<void> {
    return gwFetch<void>(`/gateway-app-releases/${id}`, { method: 'DELETE' });
}

// ── Config Releases ─────────────────────────────────────────────────

export async function listConfigReleases(params?: {
    is_active?: boolean;
    offset?: number;
    limit?: number;
}): Promise<GatewayConfigReleaseList> {
    const sp = new URLSearchParams();
    if (params?.is_active !== undefined) sp.set('is_active', String(params.is_active));
    if (params?.offset !== undefined) sp.set('offset', String(params.offset));
    if (params?.limit !== undefined) sp.set('limit', String(params.limit));
    const qs = sp.toString();
    return gwFetch<GatewayConfigReleaseList>(`/gateway-config-releases${qs ? `?${qs}` : ''}`);
}

export async function uploadConfigRelease(data: {
    version: string;
    config_payload: Record<string, unknown>;
    schema_version?: string;
    compatible_app_min?: string;
    compatible_app_max?: string;
}): Promise<GatewayConfigRelease> {
    return gwFetch<GatewayConfigRelease>('/gateway-config-releases', {
        method: 'POST',
        body: JSON.stringify(data),
    });
}

export async function toggleConfigRelease(id: string, isActive: boolean): Promise<GatewayConfigRelease> {
    return gwFetch<GatewayConfigRelease>(`/gateway-config-releases/${id}/status?is_active=${isActive}`, {
        method: 'PATCH',
    });
}

// ── Desired State ───────────────────────────────────────────────────

export async function setDesiredState(
    gatewayId: string,
    data: Record<string, unknown>
): Promise<void> {
    return gwFetch<void>(`/gateway/${gatewayId}/desired-state`, {
        method: 'PUT',
        body: JSON.stringify(data),
    });
}

// ── Commands ────────────────────────────────────────────────────────

export async function issueCommand(
    gatewayId: string,
    commandType: string,
    payload?: Record<string, unknown>
): Promise<GatewayCommand> {
    return gwFetch<GatewayCommand>(`/gateway/${gatewayId}/command`, {
        method: 'POST',
        body: JSON.stringify({ command_type: commandType, payload }),
    });
}

export async function listCommands(
    gatewayId: string,
    params?: { status?: string; offset?: number; limit?: number }
): Promise<GatewayCommandList> {
    const sp = new URLSearchParams();
    if (params?.status) sp.set('status', params.status);
    if (params?.offset !== undefined) sp.set('offset', String(params.offset));
    if (params?.limit !== undefined) sp.set('limit', String(params.limit));
    const qs = sp.toString();
    return gwFetch<GatewayCommandList>(`/gateway/${gatewayId}/commands${qs ? `?${qs}` : ''}`);
}

// ── Rollout ─────────────────────────────────────────────────────────

export async function startRollout(data: {
    release_version: string;
    target_ring?: string;
    zone_id?: string;
}): Promise<{ gateways_updated: number }> {
    return gwFetch<{ gateways_updated: number }>('/gateway-rollout', {
        method: 'POST',
        body: JSON.stringify(data),
    });
}

// ── Rollback ────────────────────────────────────────────────────────

export async function initiateRollback(
    gatewayId: string,
    targetVersion?: string
): Promise<void> {
    return gwFetch<void>(`/gateway/${gatewayId}/rollback`, {
        method: 'POST',
        body: JSON.stringify({ target_version: targetVersion || null }),
    });
}

// ── Maintenance & Block ─────────────────────────────────────────────

export async function toggleMaintenance(gatewayId: string, enabled: boolean): Promise<void> {
    return gwFetch<void>(`/gateway/${gatewayId}/maintenance?enabled=${enabled}`, {
        method: 'PUT',
    });
}

export async function toggleBlock(gatewayId: string, blocked: boolean): Promise<void> {
    return gwFetch<void>(`/gateway/${gatewayId}/block?blocked=${blocked}`, {
        method: 'PUT',
    });
}

// ── Update Logs ─────────────────────────────────────────────────────

export async function listUpdateLogs(params?: {
    gateway_id?: string;
    update_type?: string;
    offset?: number;
    limit?: number;
}): Promise<GatewayUpdateLogList> {
    const sp = new URLSearchParams();
    if (params?.gateway_id) sp.set('gateway_id', params.gateway_id);
    if (params?.update_type) sp.set('update_type', params.update_type);
    if (params?.offset !== undefined) sp.set('offset', String(params.offset));
    if (params?.limit !== undefined) sp.set('limit', String(params.limit));
    const qs = sp.toString();
    return gwFetch<GatewayUpdateLogList>(`/gateway-update-logs${qs ? `?${qs}` : ''}`);
}

// ── Audit Logs ──────────────────────────────────────────────────────

export async function listGatewayAuditLogs(params?: {
    action?: string;
    offset?: number;
    limit?: number;
}): Promise<{ items: Array<Record<string, unknown>>; total: number }> {
    const sp = new URLSearchParams();
    if (params?.action) sp.set('action', params.action);
    if (params?.offset !== undefined) sp.set('offset', String(params.offset));
    if (params?.limit !== undefined) sp.set('limit', String(params.limit));
    const qs = sp.toString();
    return gwFetch<{ items: Array<Record<string, unknown>>; total: number }>(
        `/gateway-audit-logs${qs ? `?${qs}` : ''}`
    );
}
