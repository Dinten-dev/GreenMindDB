/**
 * Unit tests for the Footer component.
 *
 * Validates rendering of copyright text and navigation links.
 * No snapshot tests – all assertions are behavioral.
 */
import { render, screen } from '@testing-library/react';
import Footer from '../Footer';

// Mock next/link to render a plain <a> tag in tests
jest.mock('next/link', () => {
    return ({ children, href }: { children: React.ReactNode; href: string }) => (
        <a href={href}>{children}</a>
    );
});

describe('Footer', () => {
    it('renders copyright with the current year', () => {
        render(<Footer />);

        const currentYear = new Date().getFullYear().toString();

        // Grenzwert: Jahreszahl muss dynamisch sein, kein Hardcode
        const copyright = screen.getByText((content) =>
            content.includes(currentYear) && content.includes('Galaxyadvisors AG'),
        );
        expect(copyright).toBeInTheDocument();
    });

    it('renders navigation links with correct hrefs', () => {
        render(<Footer />);

        const kontaktLink = screen.getByRole('link', { name: /kontakt/i });
        expect(kontaktLink).toHaveAttribute('href', '/contact');

        const impressumLink = screen.getByRole('link', { name: /impressum/i });
        expect(impressumLink).toHaveAttribute('href', '/impressum');
    });
});
