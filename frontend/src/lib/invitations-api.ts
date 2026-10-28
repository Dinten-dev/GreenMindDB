import { apiFetch } from './api';

export interface Invitation {
  id: string;
  email: string;
  delivery_status: 'pending' | 'sent' | 'failed';
  delivery_error: string | null;
  expires_at: string;
  sent_at: string | null;
  redeemed_at: string | null;
  revoked_at: string | null;
}
export interface AccessRequest {
  id: string;
  name: string;
  email: string;
  company: string | null;
  country: string | null;
  message: string | null;
  created_at: string;
  invitation: Invitation | null;
}
export interface AccessRequestPage {
  items: AccessRequest[];
  page: number;
  page_size: number;
  total: number;
}
export const listAccessRequests = (page: number) =>
  apiFetch<AccessRequestPage>('/admin/access-requests', {
    params: { page: String(page) },
    cache: 'no-store',
  });
export const sendInvitation = (submissionId: string) =>
  apiFetch<Invitation>('/admin/invitations', {
    method: 'POST',
    body: JSON.stringify({ submission_id: submissionId }),
  });
export const resendInvitation = (id: string) =>
  apiFetch<Invitation>(`/admin/invitations/${id}/resend`, { method: 'POST' });
