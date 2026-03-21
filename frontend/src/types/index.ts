/**
 * Shared TypeScript types for the GreenMind frontend.
 *
 * Add domain-specific types here (e.g., Sensor, Greenhouse, Device)
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

export interface Greenhouse {
  id: string;
  name: string;
  location: string;
  organization_id: string;
}

export interface Device {
  id: string;
  name: string;
  serial: string;
  type: string;
  status: "online" | "offline" | "pairing";
  greenhouse_id: string;
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
