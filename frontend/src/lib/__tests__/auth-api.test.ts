import { apiFetch, apiGetMe, ApiError, ApiConnectionError } from '../api';
import { advanceSessionEpoch, SESSION_INVALIDATED } from '../auth-session';
const originalFetch = global.fetch;
beforeEach(() => {
  global.fetch = jest.fn();
});
afterEach(() => {
  global.fetch = originalFetch;
});
function respond(status: number, detail = 'server detail') {
  jest.mocked(fetch).mockResolvedValue({
    ok: status < 400,
    status,
    statusText: 'error',
    json: async () => ({ detail }),
  } as Response);
}
it('uses server cookies and prevents cached session reads', async () => {
  respond(200);
  const controller = new AbortController();
  await apiGetMe(controller.signal);
  expect(fetch).toHaveBeenCalledWith(
    '/api/v1/auth/me',
    expect.objectContaining({
      credentials: 'include',
      cache: 'no-store',
      signal: controller.signal,
    })
  );
});
it('keeps HTTP errors distinct from connection failures', async () => {
  respond(503);
  await expect(apiFetch('/zones')).rejects.toMatchObject({ status: 503, detail: 'server detail' });
  jest.mocked(fetch).mockRejectedValue(new TypeError('offline'));
  await expect(apiFetch('/zones')).rejects.toBeInstanceOf(ApiConnectionError);
});
it.each(['/auth/login', '/auth/me', '/public/observe/session'])(
  'does not report sign-in/public errors as a global logout: %s',
  async (path) => {
    const listener = jest.fn();
    window.addEventListener(SESSION_INVALIDATED, listener);
    try {
      respond(401);
      await expect(apiFetch(path)).rejects.toBeInstanceOf(ApiError);
      expect(listener).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener(SESSION_INVALIDATED, listener);
    }
  }
);
it('invalidates only a current protected 401, preserving role-denial sessions', async () => {
  const listener = jest.fn();
  window.addEventListener(SESSION_INVALIDATED, listener);
  try {
    respond(403);
    await expect(apiFetch('/zones')).rejects.toBeInstanceOf(ApiError);
    expect(listener).not.toHaveBeenCalled();
    respond(401);
    await expect(apiFetch('/zones')).rejects.toBeInstanceOf(ApiError);
    expect(listener).toHaveBeenCalledTimes(1);
    let finish!: (response: Response) => void;
    jest.mocked(fetch).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        })
    );
    const pending = apiFetch('/zones');
    advanceSessionEpoch();
    finish({ ok: false, status: 401, json: async () => ({ detail: 'expired' }) } as Response);
    await expect(pending).rejects.toBeInstanceOf(ApiError);
    expect(listener).toHaveBeenCalledTimes(1);
  } finally {
    window.removeEventListener(SESSION_INVALIDATED, listener);
  }
});
