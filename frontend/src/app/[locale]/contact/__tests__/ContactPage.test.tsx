import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import ContactPage from '../page';

// Mock the API module
jest.mock('@/lib/api', () => ({
    apiSubmitContact: jest.fn(),
}));

// Import the mocked function for assertions
import { apiSubmitContact } from '@/lib/api';

const mockedSubmit = jest.mocked(apiSubmitContact);

describe('ContactPage', () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it('renders the contact form with required fields', () => {
        render(<ContactPage />);

        expect(screen.getByText('Kontaktieren Sie uns.')).toBeInTheDocument();
        expect(screen.getByPlaceholderText('Ihr Name')).toBeInTheDocument();
        expect(screen.getByPlaceholderText('sie@beispiel.com')).toBeInTheDocument();
        expect(screen.getByPlaceholderText('Wie können wir helfen?')).toBeInTheDocument();
        expect(screen.getByText('Nachricht senden')).toBeInTheDocument();
    });

    it('submits the form successfully and shows success message', async () => {
        mockedSubmit.mockResolvedValueOnce({ status: 'ok' });
        const user = userEvent.setup();

        render(<ContactPage />);

        await user.type(screen.getByPlaceholderText('Ihr Name'), 'Max Muster');
        await user.type(screen.getByPlaceholderText('sie@beispiel.com'), 'max@example.com');
        await user.type(screen.getByPlaceholderText('Wie können wir helfen?'), 'Test message');
        await user.click(screen.getByText('Nachricht senden'));

        await waitFor(() => {
            expect(screen.getByText('Nachricht gesendet!')).toBeInTheDocument();
        });

        expect(mockedSubmit).toHaveBeenCalledWith({
            name: 'Max Muster',
            email: 'max@example.com',
            company: '',
            message: 'Test message',
            website: '',
        });
    });

    it('shows error message on API failure', async () => {
        mockedSubmit.mockRejectedValueOnce(new Error('Server error'));
        const user = userEvent.setup();

        render(<ContactPage />);

        await user.type(screen.getByPlaceholderText('Ihr Name'), 'Test');
        await user.type(screen.getByPlaceholderText('sie@beispiel.com'), 'test@example.com');
        await user.type(screen.getByPlaceholderText('Wie können wir helfen?'), 'msg');
        await user.click(screen.getByText('Nachricht senden'));

        await waitFor(() => {
            expect(screen.getByText('Server error')).toBeInTheDocument();
        });
    });

    it('contains a hidden honeypot field', () => {
        render(<ContactPage />);

        const honeypot = document.querySelector('input[name="website"]');
        expect(honeypot).toBeInTheDocument();
        expect(honeypot).toHaveClass('hidden');
        expect(honeypot).toHaveAttribute('tabIndex', '-1');
    });

    it('resets form after successful submission and allows sending another', async () => {
        mockedSubmit.mockResolvedValueOnce({ status: 'ok' });
        const user = userEvent.setup();

        render(<ContactPage />);

        await user.type(screen.getByPlaceholderText('Ihr Name'), 'Test');
        await user.type(screen.getByPlaceholderText('sie@beispiel.com'), 'test@example.com');
        await user.type(screen.getByPlaceholderText('Wie können wir helfen?'), 'msg');
        await user.click(screen.getByText('Nachricht senden'));

        await waitFor(() => {
            expect(screen.getByText('Weitere Nachricht senden')).toBeInTheDocument();
        });

        await user.click(screen.getByText('Weitere Nachricht senden'));

        // Form should be visible again with empty fields
        expect(screen.getByPlaceholderText('Ihr Name')).toHaveValue('');
        expect(screen.getByPlaceholderText('sie@beispiel.com')).toHaveValue('');
    });
});
