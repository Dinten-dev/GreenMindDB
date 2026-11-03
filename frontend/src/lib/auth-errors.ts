import { ApiConnectionError, ApiError } from './api';

export type AuthIssue = 'network' | 'service' | 'expired' | 'disabled' | 'unverified';

export class SessionConfirmationError extends Error {}

export function loginErrorKey(error: unknown) {
  if (error instanceof SessionConfirmationError) return 'confirmation';
  if (error instanceof ApiConnectionError) return 'network';
  if (error instanceof ApiError) {
    if (error.status === 401 || error.status === 422) return 'credentials';
    if (error.status === 429) return 'rateLimited';
    if (error.status === 403) {
      if (/not verified/i.test(error.detail)) return 'unverified';
      if (/disabled/i.test(error.detail)) return 'disabled';
    }
  }
  return 'service';
}

export function sessionIssue(error: unknown, hadUser: boolean): AuthIssue | null {
  if (error instanceof ApiError && error.status === 401) {
    return hadUser || error.detail !== 'Not authenticated' ? 'expired' : null;
  }
  const key = loginErrorKey(error);
  return key === 'network' || key === 'disabled' || key === 'unverified' ? key : 'service';
}
