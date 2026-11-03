import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import LoginPage from '@/app/[locale]/login/page';
import { AuthProvider, useAuth } from '@/contexts/AuthContext';
import { ApiError, ApiConnectionError, apiGetMe, apiLogin, apiLogout } from '@/lib/api';

import AppLayout from '@/app/[locale]/app/layout';
import Navbar from '@/components/Navbar';
import { advanceSessionEpoch, reportUnauthorized, sessionEpoch } from '@/lib/auth-session';

const mockPush = jest.fn();
const mockReplace = jest.fn();
let mockLocale = 'en';
let mockQuery = '';
let mockPath = '/en/login';
jest.mock('next/navigation', () => ({
  useRouter: () => ({ push: mockPush, replace: mockReplace }),
  usePathname: () => mockPath,
  useSearchParams: () => new URLSearchParams(mockQuery),
}));
jest.mock('next-intl', () => ({
  useLocale: () => mockLocale,
  useTranslations: () => (key: string) => key,
}));
jest.mock('@/lib/api', () => ({
  ...jest.requireActual('@/lib/api'),
  apiGetMe: jest.fn(),
  apiLogin: jest.fn(),
  apiLogout: jest.fn(),
}));
const user = {
  id: 'synthetic-account',
  email: 'login@example.invalid',
  name: 'Test User',
  role: 'member',
  organization_id: 'org',
  organization_name: 'Test',
  is_active: true,
  phone_number: null,
};

beforeEach(() => {
  jest.clearAllMocks();
  mockLocale = 'en';
  mockQuery = '';
  mockPath = '/en/login';
  window.history.replaceState({}, '', '/en/login');
  jest.mocked(apiGetMe).mockReset().mockRejectedValue(new ApiError(401, 'Not authenticated'));
  jest.mocked(apiLogin).mockReset();
  jest.mocked(apiLogout).mockReset().mockResolvedValue();
  jest.mocked(apiLogin).mockResolvedValue({ user, detail: 'OK' });
});

it('keeps the selected language after sign in', async () => {
  jest
    .mocked(apiGetMe)
    .mockRejectedValueOnce(new ApiError(401, 'Not authenticated'))
    .mockResolvedValue(user);
  const { container } = render(
    <AuthProvider>
      <LoginPage />
    </AuthProvider>
  );
  await waitFor(() => expect(apiGetMe).toHaveBeenCalled());
  fireEvent.change(container.querySelector('input[type="email"]')!, {
    target: { value: user.email },
  });
  fireEvent.change(container.querySelector('input[type="password"]')!, {
    target: { value: 'synthetic-password' },
  });
  fireEvent.submit(container.querySelector('form')!);
  await waitFor(() =>
    expect([...mockPush.mock.calls, ...mockReplace.mock.calls]).toContainEqual([
      '/en/app/dashboard',
    ])
  );
});

it('does not let an older session check undo a successful login', async () => {
  let rejectOld!: (reason: Error) => void;
  jest
    .mocked(apiGetMe)
    .mockImplementationOnce(
      () =>
        new Promise((_, reject) => {
          rejectOld = reject;
        })
    )
    .mockResolvedValue(user);
  function Probe() {
    const auth = useAuth();
    return (
      <>
        <span>{auth.user?.name || 'anonymous'}</span>
        <button onClick={() => auth.login(user.email, 'synthetic-password')}>sign in</button>
      </>
    );
  }
  render(
    <AuthProvider>
      <Probe />
    </AuthProvider>
  );
  await waitFor(() => expect(apiGetMe).toHaveBeenCalled());
  fireEvent.click(screen.getByText('sign in'));
  await screen.findByText('Test User');
  await act(async () => rejectOld(new Error('Not authenticated')));
  expect(screen.getByText('Test User')).toBeInTheDocument();
});

function fillAndSubmit(container: HTMLElement) {
  fireEvent.change(container.querySelector('#login-email')!, { target: { value: user.email } });
  fireEvent.change(container.querySelector('#login-password')!, { target: { value: 'short' } });
  fireEvent.submit(container.querySelector('form')!);
}

it.each(['de', 'en', 'fr'])(
  'restores a protected destination in %s after cookie confirmation',
  async (locale) => {
    mockLocale = locale;
    mockQuery = new URLSearchParams({ next: `/${locale}/app/sensors?view=24h` }).toString();
    jest
      .mocked(apiGetMe)
      .mockRejectedValueOnce(new ApiError(401, 'Not authenticated'))
      .mockResolvedValue(user);
    const { container } = render(
      <AuthProvider>
        <Navbar />
        <LoginPage />
      </AuthProvider>
    );
    await waitFor(() => expect(apiGetMe).toHaveBeenCalledTimes(1));
    fillAndSubmit(container);
    await waitFor(() =>
      expect(mockReplace).toHaveBeenCalledWith(`/${locale}/app/sensors?view=24h`)
    );
    expect(screen.getByRole('link', { name: /Test User/ })).toHaveAttribute(
      'href',
      `/${locale}/app/dashboard`
    );
    expect(apiLogin).toHaveBeenCalledWith(user.email, 'short');
  }
);

it.each([
  [new ApiError(401, 'raw server detail'), 'credentials'],
  [new ApiError(422, 'validation detail'), 'credentials'],
  [new ApiError(403, 'Email not verified'), 'unverified'],
  [new ApiError(403, 'Account disabled'), 'disabled'],
  [new ApiError(429, 'rate limit'), 'rateLimited'],
  [new ApiError(503, 'raw database detail'), 'service'],
  [new ApiConnectionError(), 'network'],
])('presents a distinct sign-in error: %s', async (error, key) => {
  jest.mocked(apiLogin).mockRejectedValue(error);
  const { container } = render(
    <AuthProvider>
      <LoginPage />
    </AuthProvider>
  );
  await waitFor(() => expect(apiGetMe).toHaveBeenCalledTimes(1));
  fillAndSubmit(container);
  expect(await screen.findByRole('alert')).toHaveTextContent(`errors.${key}`);
  expect(mockReplace).not.toHaveBeenCalled();
});

it('requires the cookie to work before redirecting', async () => {
  const { container } = render(
    <AuthProvider>
      <LoginPage />
    </AuthProvider>
  );
  await waitFor(() => expect(apiGetMe).toHaveBeenCalledTimes(1));
  fillAndSubmit(container);
  expect(await screen.findByRole('alert')).toHaveTextContent('errors.confirmation');
  expect(mockReplace).not.toHaveBeenCalled();
});

it('prevents duplicate submissions and offers password visibility', async () => {
  let finish!: () => void;
  jest.mocked(apiLogin).mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = () => resolve({ user, detail: 'OK' });
      })
  );
  const { container } = render(
    <AuthProvider>
      <LoginPage />
    </AuthProvider>
  );
  await waitFor(() => expect(apiGetMe).toHaveBeenCalledTimes(1));
  expect(screen.getByLabelText('email')).toHaveAttribute('autocomplete', 'username');
  expect(screen.getByLabelText('password')).toHaveAttribute('autocomplete', 'current-password');
  fireEvent.click(screen.getByRole('button', { name: 'showPassword' }));
  expect(screen.getByLabelText('password')).toHaveAttribute('type', 'text');
  fillAndSubmit(container);
  fireEvent.submit(container.querySelector('form')!);
  expect(apiLogin).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('button', { name: 'signingIn' })).toBeDisabled();
  await act(async () => finish());
});

it('restores the account on reload without another sign in', async () => {
  jest.mocked(apiGetMe).mockResolvedValue(user);
  render(
    <AuthProvider>
      <LoginPage />
    </AuthProvider>
  );
  await waitFor(() => expect(mockReplace).toHaveBeenCalledWith('/en/app/dashboard'));
  expect(apiLogin).not.toHaveBeenCalled();
});

it('keeps a network failure on a protected page retryable without redirecting', async () => {
  mockPath = '/en/app/sensors';
  jest.mocked(apiGetMe).mockRejectedValueOnce(new ApiConnectionError()).mockResolvedValue(user);
  render(
    <AuthProvider>
      <AppLayout>
        <p>Protected content</p>
      </AppLayout>
    </AuthProvider>
  );
  expect(await screen.findByRole('alert')).toHaveTextContent('errors.network');
  expect(mockReplace).not.toHaveBeenCalled();
  expect(screen.queryByText('Protected content')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'retry' }));
  expect(await screen.findByText('Protected content')).toBeInTheDocument();
});

it('redirects expired protected sessions to localized sign in with destination', async () => {
  mockLocale = 'fr';
  mockPath = '/fr/app/sensors';
  window.history.replaceState({}, '', '/fr/app/sensors?view=24h');
  jest.mocked(apiGetMe).mockResolvedValue(user);
  render(
    <AuthProvider>
      <AppLayout>
        <p>Protected content</p>
      </AppLayout>
    </AuthProvider>
  );
  await screen.findByText('Protected content');
  await act(async () => reportUnauthorized(sessionEpoch()));
  expect(screen.queryByText('Protected content')).not.toBeInTheDocument();
  expect(mockReplace).toHaveBeenCalledWith(
    '/fr/login?next=%2Ffr%2Fapp%2Fsensors%3Fview%3D24h&reason=expired'
  );
});

it('ignores an old unauthorized request after a newer login', async () => {
  const oldEpoch = sessionEpoch();
  jest.mocked(apiGetMe).mockResolvedValue(user);
  render(
    <AuthProvider>
      <LoginPage />
    </AuthProvider>
  );
  await waitFor(() => expect(mockReplace).toHaveBeenCalled());
  advanceSessionEpoch();
  mockReplace.mockClear();
  await act(async () => reportUnauthorized(oldEpoch));
  expect(mockReplace).not.toHaveBeenCalled();
  expect(screen.queryByText('errors.expired')).not.toBeInTheDocument();
});

it('does not resurrect a logged out account from an older refresh', async () => {
  let resolveOld!: (value: typeof user) => void;
  function Probe() {
    const auth = useAuth();
    return (
      <>
        <span>{auth.user?.name || 'anonymous'}</span>
        <button onClick={() => void auth.refresh()}>refresh</button>
        <button onClick={() => void auth.logout()}>logout</button>
      </>
    );
  }
  jest
    .mocked(apiGetMe)
    .mockResolvedValueOnce(user)
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveOld = resolve;
        })
    );
  render(
    <AuthProvider>
      <Probe />
    </AuthProvider>
  );
  await screen.findByText('Test User');
  fireEvent.click(screen.getByText('refresh'));
  await waitFor(() => expect(apiGetMe).toHaveBeenCalledTimes(2));
  fireEvent.click(screen.getByText('logout'));
  await screen.findByText('anonymous');
  await act(async () => resolveOld(user));
  expect(screen.getByText('anonymous')).toBeInTheDocument();
});

it('retains the account when logout cannot reach the server', async () => {
  function Probe() {
    const auth = useAuth();
    return (
      <>
        <span>{auth.user?.name || 'anonymous'}</span>
        <span>{auth.status}</span>
        <button onClick={() => void auth.logout().catch(() => {})}>logout</button>
      </>
    );
  }
  jest.mocked(apiGetMe).mockResolvedValue(user);
  jest.mocked(apiLogout).mockRejectedValue(new ApiConnectionError());
  render(
    <AuthProvider>
      <Probe />
    </AuthProvider>
  );
  await screen.findByText('Test User');
  fireEvent.click(screen.getByText('logout'));
  await screen.findByText('unavailable');
  expect(screen.getByText('Test User')).toBeInTheDocument();
});

it('preserves and localizes the validated return destination when switching language', async () => {
  window.history.replaceState(
    {},
    '',
    '/en/login?next=%2Fen%2Fapp%2Fsensors%3Fview%3D24h&reason=expired'
  );
  render(
    <AuthProvider>
      <Navbar />
    </AuthProvider>
  );
  await screen.findByRole('combobox');
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'fr' } });
  expect(mockPush).toHaveBeenCalledWith(
    '/fr/login?next=%2Ffr%2Fapp%2Fsensors%3Fview%3D24h&reason=expired'
  );
});

it('recovers a known account after a background service failure', async () => {
  function Probe() {
    const auth = useAuth();
    return (
      <>
        <span>{auth.user?.name || 'anonymous'}</span>
        <span>{auth.status}</span>
        <button onClick={() => void auth.refresh()}>refresh</button>
      </>
    );
  }
  jest
    .mocked(apiGetMe)
    .mockResolvedValueOnce(user)
    .mockRejectedValueOnce(new ApiError(503, 'unavailable'))
    .mockResolvedValue(user);
  render(
    <AuthProvider>
      <Probe />
    </AuthProvider>
  );
  await screen.findByText('Test User');
  fireEvent.click(screen.getByText('refresh'));
  await screen.findByText('unavailable');
  expect(screen.getByText('Test User')).toBeInTheDocument();
  fireEvent.click(screen.getByText('refresh'));
  await screen.findByText('authenticated');
});

it('checks the authoritative session again when the tab regains focus', async () => {
  jest
    .mocked(apiGetMe)
    .mockResolvedValueOnce(user)
    .mockRejectedValueOnce(new ApiError(401, 'Not authenticated'));
  mockPath = '/en/app/sensors';
  render(
    <AuthProvider>
      <AppLayout>
        <p>Protected content</p>
      </AppLayout>
    </AuthProvider>
  );
  await screen.findByText('Protected content');
  fireEvent.focus(window);
  await waitFor(() =>
    expect(mockReplace).toHaveBeenCalledWith(expect.stringContaining('reason=expired'))
  );
  expect(screen.queryByText('Protected content')).not.toBeInTheDocument();
});

it('restores a session under React StrictMode effect replay', async () => {
  jest.mocked(apiGetMe).mockResolvedValue(user);
  render(
    <React.StrictMode>
      <AuthProvider>
        <LoginPage />
      </AuthProvider>
    </React.StrictMode>
  );
  await waitFor(() => expect(mockReplace).toHaveBeenCalledWith('/en/app/dashboard'));
});

it('returns to the localized homepage after an intentional logout', async () => {
  mockLocale = 'fr';
  mockPath = '/fr/app/account';
  jest.mocked(apiGetMe).mockResolvedValue(user);
  render(
    <AuthProvider>
      <AppLayout>
        <p>Protected content</p>
      </AppLayout>
    </AuthProvider>
  );
  await screen.findByText('Protected content');
  fireEvent.click(screen.getAllByRole('button', { name: 'Abmelden' })[0]);
  await waitFor(() => expect(mockReplace).toHaveBeenCalledWith('/fr'));
  expect(mockReplace.mock.calls.every(([target]) => !target.includes('/login'))).toBe(true);
});
