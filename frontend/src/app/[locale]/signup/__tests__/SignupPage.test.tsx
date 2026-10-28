import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import SignupPage from '../page';
import { apiSignup } from '@/lib/api';

const replace = jest.fn();
const router = { replace };
jest.mock('next/navigation', () => ({ useRouter: () => router }));
jest.mock('next-intl', () => ({ useLocale: () => 'de' }));
jest.mock('@/lib/api', () => ({ apiSignup: jest.fn() }));
jest.mock(
  '@/components/BrandLogo',
  () =>
    function Logo() {
      return null;
    }
);

beforeEach(() => {
  jest.clearAllMocks();
  window.history.replaceState(null, '', '/de/signup');
});

test('signup without invitation redirects to access request', async () => {
  render(<SignupPage />);
  await waitFor(() => expect(replace).toHaveBeenCalledWith('/de/early-access'));
  expect(screen.queryByRole('button', { name: 'Konto erstellen' })).not.toBeInTheDocument();
});

test('submits fragment token without allowing an email override', async () => {
  const token = 'a'.repeat(64);
  window.history.replaceState(null, '', `/de/signup#invitation_token=${token}`);
  (apiSignup as jest.Mock).mockResolvedValue({ detail: 'Invitation accepted.' });
  render(<SignupPage />);
  expect(window.location.hash).toBe('');
  expect(screen.queryByPlaceholderText('sie@beispiel.com')).not.toBeInTheDocument();
  const user = userEvent.setup();
  await user.type(screen.getByPlaceholderText('Ihr Name'), 'Invitee');
  await user.type(
    screen.getByPlaceholderText('Mind. 8 Zeichen, Gross- + Kleinbuchstaben + Zahl'),
    'SecurePass123'
  );
  await user.click(screen.getByRole('button', { name: 'Konto erstellen' }));
  expect(apiSignup).toHaveBeenCalledWith(token, 'SecurePass123', 'Invitee');
  await screen.findByText('Invitation accepted.');
  expect(screen.getByRole('button', { name: 'Konto erstellen' })).toBeDisabled();
});
