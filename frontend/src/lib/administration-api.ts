import { apiFetch } from './api';

export type StorageStatus = {
  total_bytes: number;
  available_bytes: number;
  used_bytes: number;
  free_percent: number;
  used_percent: number;
  warning: boolean;
  warning_threshold_percent: number;
  checked_at: string;
};
export type ManagedUser = {
  id: string;
  email: string;
  name: string | null;
  phone_number: string | null;
  role: 'member' | 'owner' | 'admin';
  organization_id: string | null;
  organization_name: string | null;
  is_active: boolean;
  is_verified: boolean;
  zone_ids: string[];
  visible_zones: { id: string; name: string }[];
  access_note: string;
  all_zones: boolean;
  protected: boolean;
};
export type AdminCatalog = {
  companies: { id: string; name: string }[];
  zones: { id: string; name: string; organization_id: string }[];
};
export const adminCapabilities = () =>
  apiFetch<{ can_manage: boolean }>('/administration/capabilities');
export const adminStorage = () => apiFetch<StorageStatus>('/administration/storage');
export type ArchiveOverview = {
  environment: string;
  state: 'unavailable' | 'idle' | 'running' | 'paused' | 'failed';
  configured: boolean;
  manual_copy_available: boolean;
  files_copied: number | null;
  pending_bytes: number | null;
  transfer_bytes_per_second: number | null;
  worker_memory_bytes: number | null;
  worker_memory_limit_bytes: number | null;
  worker_memory_reserve_bytes: number | null;
  storage_box_used_bytes: number | null;
  storage_box_total_bytes: number | null;
  sampled_at: string | null;
  message: string;
};
export const adminArchiveStatus = () => apiFetch<ArchiveOverview>('/administration/archive/status');
export const adminRequestArchiveCopy = () =>
  apiFetch<{ status: string }>('/administration/archive/copy', { method: 'POST' });
export const adminCatalog = () => apiFetch<AdminCatalog>('/administration/catalog');
export const adminUsers = (search: string, offset: number) =>
  apiFetch<{ users: ManagedUser[]; total: number }>('/administration/users', {
    params: { search, offset: String(offset), limit: '50' },
  });
export type UserAssignment = {
  name: string;
  organization_id: string;
  role: ManagedUser['role'];
  zone_ids: string[];
};
export const adminCreateUser = (data: UserAssignment & { email: string; password: string }) =>
  apiFetch<ManagedUser>('/administration/users', { method: 'POST', body: JSON.stringify(data) });
export const adminUpdateUser = (
  id: string,
  data: UserAssignment & { phone_number: string | null; is_active: boolean }
) =>
  apiFetch<ManagedUser>(`/administration/users/${id}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });

export const adminDeleteUser = (id: string, confirmation_email: string) =>
  apiFetch<void>(`/administration/users/${id}`, {
    method: 'DELETE',
    body: JSON.stringify({ confirmation_email }),
  });
export const adminUpdateCompany = (id: string, name: string) =>
  apiFetch<{ id: string; name: string }>(`/administration/companies/${id}`, {
    method: 'PUT',
    body: JSON.stringify({ name }),
  });
