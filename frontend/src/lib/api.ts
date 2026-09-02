const API_BASE =
  typeof window === 'undefined'
    ? `${process.env.INTERNAL_API_URL || 'http://localhost:8000'}/api/v1`
    : '/api/v1';

export interface FetchOptions extends RequestInit {
  params?: Record<string, string>;
}

export async function apiFetch<T>(path: string, options: FetchOptions = {}): Promise<T> {
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
  phone_number: string | null;
}

interface AuthResponse {
  detail: string;
  user: AuthUser;
}

export async function apiSignup(
  email: string,
  password: string,
  name: string
): Promise<AuthResponse> {
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

export async function apiVerifyEmail(token: string): Promise<{ detail: string }> {
  return apiFetch<{ detail: string }>('/auth/verify-email', {
    method: 'POST',
    body: JSON.stringify({ token }),
  });
}

export async function apiLogout(): Promise<void> {
  await apiFetch('/auth/logout', { method: 'POST' });
}

export async function apiGetMe(): Promise<AuthUser> {
  return apiFetch<AuthUser>('/auth/me');
}

export async function apiUpdateMe(data: {
  name?: string;
  phone_number?: string;
}): Promise<AuthUser> {
  return apiFetch<AuthUser>('/auth/me', {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
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

// ── Zones (Agriculture Areas) ────────────────
export interface Zone {
  id: string;
  name: string;
  location: string | null;
  zone_type: string;
  latitude: number | null;
  longitude: number | null;
  created_at: string;
  gateway_count: number;
  sensor_count: number;
}

export interface ZoneOverview {
  id: string;
  name: string;
  zone_type: string;
  total_gateways: number;
  online_gateways: number;
  total_sensors: number;
  readings_24h: number;
}

export const ZONE_TYPES = [
  { value: 'GREENHOUSE', label: 'Gewächshaus', icon: '🏠' },
  { value: 'OPEN_FIELD', label: 'Freiland', icon: '🌾' },
  { value: 'VERTICAL_FARM', label: 'Vertical Farm', icon: '🏢' },
  { value: 'ORCHARD', label: 'Obstgarten', icon: '🍎' },
] as const;

export async function apiListZones(): Promise<Zone[]> {
  return apiFetch<Zone[]>('/zones');
}

export async function apiCreateZone(
  name: string,
  zone_type: string = 'GREENHOUSE',
  location?: string,
  latitude?: number,
  longitude?: number
): Promise<Zone> {
  return apiFetch<Zone>('/zones', {
    method: 'POST',
    body: JSON.stringify({ name, zone_type, location, latitude, longitude }),
  });
}

export async function apiDeleteZone(id: string): Promise<void> {
  return apiFetch<void>(`/zones/${id}`, { method: 'DELETE' });
}

export async function apiGetZoneOverview(id: string): Promise<ZoneOverview> {
  return apiFetch<ZoneOverview>(`/zones/${id}/overview`);
}

// ── Gateways ─────────────────────────────────
export interface GatewayInfo {
  id: string;
  zone_id: string;
  zone_name: string | null;
  hardware_id: string;
  name: string | null;
  local_ip: string | null;
  fw_version: string | null;
  status: string;
  is_active: boolean;
  last_seen: string | null;
  paired_at: string | null;
  sensor_count: number;
  wav_pending_files: number | null;
  wav_pending_bytes: number | null;
  wav_oldest_pending_age_hours: number | null;
  wav_last_upload_at: string | null;
  wav_last_error_code: string | null;
}

export function gatewayHasWavIssue(gateway: GatewayInfo): boolean {
  return Boolean(
    gateway.wav_last_error_code ||
      (gateway.wav_pending_files ?? 0) >= 6 ||
      (gateway.wav_oldest_pending_age_hours ?? 0) >= 1
  );
}

export interface PairingCode {
  code: string;
  expires_at: string;
  zone_id: string;
}

export async function apiListGateways(zone_id?: string): Promise<GatewayInfo[]> {
  const params = zone_id ? { zone_id } : undefined;
  return apiFetch<GatewayInfo[]>('/gateways', { params });
}

export async function apiDeleteGateway(gatewayId: string): Promise<void> {
  return apiFetch<void>(`/gateways/${gatewayId}`, { method: 'DELETE' });
}

export async function apiGeneratePairingCode(zone_id: string): Promise<PairingCode> {
  return apiFetch<PairingCode>('/gateways/pairing-code', {
    method: 'POST',
    body: JSON.stringify({ zone_id }),
  });
}

// ── Sensors (ESP32) ──────────────────────────
export interface SensorInfo {
  id: string;
  gateway_id: string;
  zone_id: string | null;
  mac_address: string;
  name: string | null;
  sensor_type: string;
  status: string;
  last_seen: string | null;
  claimed_at: string | null;
  gateway_name: string | null;
  gateway_hardware_id: string | null;
  sms_alerts_enabled: boolean;
}

export async function apiListSensors(zone_id?: string, gateway_id?: string): Promise<SensorInfo[]> {
  const params: Record<string, string> = {};
  if (zone_id) params.zone_id = zone_id;
  if (gateway_id) params.gateway_id = gateway_id;
  return apiFetch<SensorInfo[]>('/sensors', {
    params: Object.keys(params).length ? params : undefined,
  });
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

export async function apiGetSensorData(
  sensorId: string,
  range: string = '24h'
): Promise<SensorDataResponse[]> {
  return apiFetch<SensorDataResponse[]>(`/sensors/${sensorId}/data`, { params: { range } });
}

export async function apiDeleteSensor(sensorId: string): Promise<void> {
  return apiFetch<void>(`/sensors/${sensorId}`, { method: 'DELETE' });
}

export async function apiUpdateSensor(
  sensorId: string,
  data: { name?: string; sms_alerts_enabled?: boolean }
): Promise<SensorInfo> {
  return apiFetch<SensorInfo>(`/sensors/${sensorId}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
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

export async function apiSubmitContact(payload: ContactPayload): Promise<{ status: string }> {
  return apiFetch<{ status: string }>('/contact', {
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

export async function apiSubmitEarlyAccess(
  payload: EarlyAccessPayload
): Promise<{ status: string }> {
  return apiFetch<{ status: string }>('/early-access', {
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

// ── WAV Files ────────────────────────────────────
export interface WavFileInfo {
  id: string;
  sensor_mac: string;
  sensor_id: string;
  sample_rate: number;
  duration_seconds: number;
  file_size_bytes: number;
  started_at: string;
  ended_at: string;
  created_at: string;
  timestamp_source: string;
  coverage_ratio: number;
  timing_status: string;
  raw_available: boolean;
  raw_deleted_at: string | null;
  feature_status: 'pending' | 'processing' | 'verified' | 'failed';
  feature_verified_at: string | null;
  is_anomaly: boolean | null;
  anomaly_score: number | null;
  flac_available: boolean;
}

export interface WavFeatureInfo {
  id: string;
  wav_file_id: string;
  sensor_id: string;
  sensor_mac: string;
  started_at: string;
  ended_at: string;
  extractor_version: string;
  feature_checksum: string;
  sample_rate: number;
  sample_count: number;
  duration_seconds: number;
  value_unit: string;
  mean: number;
  median: number;
  rms: number;
  standard_deviation: number;
  minimum: number;
  maximum: number;
  quantiles: Record<string, number>;
  outlier_count: number;
  outlier_ratio: number;
  clipping_count: number;
  clipping_ratio: number;
  coverage_ratio: number;
  timing_status: string;
  missing_duration_seconds: number;
  flatline_count: number;
  flatline_seconds: number;
  sequence_observations: number;
  sequence_gap_count: number;
  sequence_missing_count: number;
  sequence_reset_count: number;
  source_dropped_samples_delta: number | null;
  spectral_energy_total: number;
  dominant_frequency_hz: number;
  spectral_bands: Record<string, number>;
  is_anomaly: boolean;
  anomaly_score: number;
  anomaly_reasons: string[];
  anomaly_archive_available: boolean;
  flac_archive_available: boolean;
  verified_at: string;
}

export async function apiListWavFiles(
  sensorId: string,
  opts?: { from_dt?: string; to_dt?: string; limit?: number }
): Promise<WavFileInfo[]> {
  const params: Record<string, string> = { sensor_id: sensorId };
  if (opts?.from_dt) params.from_dt = opts.from_dt;
  if (opts?.to_dt) params.to_dt = opts.to_dt;
  if (opts?.limit) params.limit = String(opts.limit);
  return apiFetch<WavFileInfo[]>('/wav/files', { params });
}

export async function apiListWavFeatures(
  sensorId: string,
  opts?: { from_dt?: string; to_dt?: string; anomalies_only?: boolean; limit?: number }
): Promise<WavFeatureInfo[]> {
  const params: Record<string, string> = { sensor_id: sensorId };
  if (opts?.from_dt) params.from_dt = opts.from_dt;
  if (opts?.to_dt) params.to_dt = opts.to_dt;
  if (opts?.anomalies_only) params.anomalies_only = 'true';
  if (opts?.limit) params.limit = String(opts.limit);
  return apiFetch<WavFeatureInfo[]>('/wav/features', { params });
}

export interface WavCountInfo {
  count: number;
  total_bytes: number;
}

export async function apiCountWavFiles(
  sensorId: string,
  fromDt?: string,
  toDt?: string
): Promise<WavCountInfo> {
  const params: Record<string, string> = { sensor_id: sensorId };
  if (fromDt) params.from_dt = fromDt;
  if (toDt) params.to_dt = toDt;
  return apiFetch<WavCountInfo>('/wav/count', { params });
}

export async function apiDownloadWav(wavId: string): Promise<void> {
  const url = `${API_BASE}/wav/download/${wavId}`;
  const res = await fetch(url, { credentials: 'include' });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body.detail || `Download failed ${res.status}`);
  }

  const blob = await res.blob();
  const disposition = res.headers.get('Content-Disposition') || '';
  const filenameMatch = disposition.match(/filename="?([^"]+)"?/);
  const filename = filenameMatch ? filenameMatch[1] : `${wavId}.wav`;

  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(a.href);
}

export async function apiDownloadWavBundle(
  sensorId: string,
  fromDt: string,
  toDt: string
): Promise<void> {
  const params = new URLSearchParams({
    sensor_id: sensorId,
    from_dt: fromDt,
    to_dt: toDt,
  });
  const url = `${API_BASE}/wav/download-bundle?${params}`;
  const res = await fetch(url, { credentials: 'include' });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body.detail || `Bundle download failed ${res.status}`);
  }

  const blob = await res.blob();
  const disposition = res.headers.get('Content-Disposition') || '';
  const filenameMatch = disposition.match(/filename="?([^"]+)"?/);
  const filename = filenameMatch ? filenameMatch[1] : `greenmind_bundle.zip`;

  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(a.href);
}

// ── Sensor WebSocket (Live Streaming) ────────────
export interface SensorWebSocketMessage {
  event: string;
  sensor_id: string;
  sensor_mac: string;
  readings: Array<{
    value: number;
    unit: string;
    kind: string;
    timestamp: string;
  }>;
}

export function createSensorWebSocket(
  sensorId: string,
  onMessage: (msg: SensorWebSocketMessage) => void,
  onStatusChange?: (connected: boolean) => void
): () => void {
  let ws: WebSocket | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let reconnectAttempts = 0;
  let intentionalClose = false;

  const wsBase =
    typeof window !== 'undefined'
      ? `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}`
      : 'ws://localhost:8000';

  function connect() {
    if (intentionalClose) return;

    ws = new WebSocket(`${wsBase}/api/v1/ws/sensor/${sensorId}`);

    ws.onopen = () => {
      reconnectAttempts = 0;
      onStatusChange?.(true);
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data) as SensorWebSocketMessage;
        onMessage(msg);
      } catch {
        // Ignore unparseable messages
      }
    };

    ws.onclose = () => {
      onStatusChange?.(false);
      if (!intentionalClose) {
        const delay = Math.min(1000 * 2 ** reconnectAttempts, 30000);
        reconnectAttempts++;
        reconnectTimer = setTimeout(connect, delay);
      }
    };

    ws.onerror = () => {
      ws?.close();
    };
  }

  connect();

  // Return teardown function
  return () => {
    intentionalClose = true;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    ws?.close();
  };
}
