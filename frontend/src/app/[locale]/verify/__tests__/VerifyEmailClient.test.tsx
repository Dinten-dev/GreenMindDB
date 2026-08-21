import { render, screen, waitFor } from '@testing-library/react';
import VerifyEmailClient from '../VerifyEmailClient';
import { apiVerifyEmail } from '@/lib/api';

jest.mock('@/lib/api', () => ({
  apiVerifyEmail: jest.fn(),
}));

const mockedVerify = jest.mocked(apiVerifyEmail);

describe('VerifyEmailClient', () => {
  beforeEach(() => {
    mockedVerify.mockReset();
  });

  it('rejects malformed links without contacting the API', () => {
    render(<VerifyEmailClient token="not-a-token" />);

    expect(screen.getByText('Der Bestätigungslink ist ungültig.')).toBeInTheDocument();
    expect(mockedVerify).not.toHaveBeenCalled();
  });

  it('verifies a valid one-time token', async () => {
    const token = 'a'.repeat(32);
    window.history.replaceState(null, '', `/verify?token=${token}`);
    mockedVerify.mockResolvedValue({ detail: 'Email successfully verified' });

    render(<VerifyEmailClient token={token} />);

    await waitFor(() => expect(mockedVerify).toHaveBeenCalledWith(token));
    expect(window.location.search).toBe('');
    expect(await screen.findByText('Email successfully verified')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Zur Anmeldung' })).toHaveAttribute('href', '/login');
  });
});
