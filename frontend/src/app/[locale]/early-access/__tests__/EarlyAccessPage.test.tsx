import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import EarlyAccessPage from '../page';

jest.mock('@/lib/api', () => ({
    apiSubmitEarlyAccess: jest.fn(),
}));

import { apiSubmitEarlyAccess } from '@/lib/api';

const mockedSubmit = jest.mocked(apiSubmitEarlyAccess);

describe('EarlyAccessPage', () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it('renders the early access form with required fields', () => {
        render(<EarlyAccessPage />);

        expect(screen.getByRole('heading', { name: /zugang anfragen/i })).toBeInTheDocument();
        expect(screen.getByPlaceholderText('Ihr Name')).toBeInTheDocument();
        expect(screen.getByPlaceholderText('sie@beispiel.com')).toBeInTheDocument();
        expect(screen.getByPlaceholderText('Unternehmen oder Betrieb')).toBeInTheDocument();
    });

    it('submits the form and shows success state', async () => {
        mockedSubmit.mockResolvedValueOnce({ status: 'ok' });
        const user = userEvent.setup();

        render(<EarlyAccessPage />);

        await user.type(screen.getByPlaceholderText('Ihr Name'), 'Anna Test');
        await user.type(screen.getByPlaceholderText('sie@beispiel.com'), 'anna@example.com');
        await user.type(screen.getByPlaceholderText('Unternehmen oder Betrieb'), 'TestCo');
        await user.selectOptions(screen.getByRole('combobox'), 'Schweiz');
        await user.click(screen.getByRole('button', { name: /zugang anfragen/i }));

        await waitFor(() => {
            expect(screen.getByText('Anfrage eingegangen!')).toBeInTheDocument();
        });

        expect(mockedSubmit).toHaveBeenCalledWith({
            name: 'Anna Test',
            email: 'anna@example.com',
            company: 'TestCo',
            country: 'Schweiz',
            message: '',
            website: '',
        });
    });

    it('shows error message when submission fails', async () => {
        mockedSubmit.mockRejectedValueOnce(new Error('Network failure'));
        const user = userEvent.setup();

        render(<EarlyAccessPage />);

        await user.type(screen.getByPlaceholderText('Ihr Name'), 'Test');
        await user.type(screen.getByPlaceholderText('sie@beispiel.com'), 'test@example.com');
        await user.type(screen.getByPlaceholderText('Unternehmen oder Betrieb'), 'Co');
        await user.selectOptions(screen.getByRole('combobox'), 'Deutschland');
        await user.click(screen.getByRole('button', { name: /zugang anfragen/i }));

        await waitFor(() => {
            expect(screen.getByText('Network failure')).toBeInTheDocument();
        });
    });

    it('contains a hidden honeypot field', () => {
        render(<EarlyAccessPage />);

        const honeypot = document.querySelector('input[name="website"]');
        expect(honeypot).toBeInTheDocument();
        expect(honeypot).toHaveClass('hidden');
        expect(honeypot).toHaveAttribute('tabIndex', '-1');
    });
});
