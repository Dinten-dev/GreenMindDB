/**
 * Shared TypeScript types for the GreenMind frontend.
 *
 * Add domain-specific types here (e.g., Sensor, Zone, Device)
 * so they can be imported across components and pages.
 */

// ─── Auth ───────────────────────────────────────────────────────────

export interface User {
  id: string;
  email: string;
  name: string;
  role: "admin" | "operator" | "viewer";
  organization_id: string;
  is_active: boolean;
}

// ─── Core Domain ────────────────────────────────────────────────────

export interface Organization {
  id: string;
  name: string;
}

export interface Zone {
  id: string;
  name: string;
  location: string;
  zone_type: string;
  organization_id: string;
}

export interface Device {
  id: string;
  name: string;
  serial: string;
  type: string;
  status: "online" | "offline" | "pairing";
  zone_id: string;
}

export interface Sensor {
  id: string;
  device_id: string;
  kind: string;
  unit: string;
  label?: string;
}

// ─── Timeseries ─────────────────────────────────────────────────────

export interface SensorReading {
  timestamp: string;
  value: number;
  sensor_id: string;
}

// ─── Plants & Observations ────────────────────────────────────────────

export interface Plant {
  id: string;
  organization_id: string;
  zone_id: string;
  name: string;
  plant_code: string | null;
  species: string | null;
  cultivar: string | null;
  description: string | null;
  planted_at: string | null;
  status: string;
  created_at: string;
  updated_at: string;
  current_sensor_id: string | null;
}

export interface PlantSensorAssignment {
  id: string;
  plant_id: string;
  sensor_id: string;
  assigned_at: string;
  unassigned_at: string | null;
  notes: string | null;
  is_active: boolean;
}

export interface ObservationAccess {
  id: string;
  plant_id: string;
  public_id: string;
  is_active: boolean;
  usage_count: number;
  revoked_at: string | null;
}

export interface PlantObservationPhoto {
  id: string;
  observation_id: string;
  storage_key: string;
  mime_type: string;
  file_size: number;
  created_at: string;
}

export interface PlantObservation {
  id: string;
  plant_id: string;
  sensor_id: string | null;
  zone_id: string;
  observed_at: string;
  wellbeing_score: number;
  stress_score: number | null;
  plant_condition: string;
  leaf_droop: boolean | null;
  leaf_color: string | null;
  spots_present: boolean | null;
  soil_condition: string | null;
  suspected_stress_type: string | null;
  notes: string | null;
  created_at: string;
  photos: PlantObservationPhoto[];
}

export interface PublicPlantContext {
  plant_id: string;
  name: string;
  plant_code: string | null;
  zone_name: string | null;
}

// ─── API Responses ──────────────────────────────────────────────────

export interface ApiError {
  detail: string;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  per_page: number;
}
