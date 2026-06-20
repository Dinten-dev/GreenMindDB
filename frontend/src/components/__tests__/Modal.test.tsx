/**
 * Unit tests for the Modal component.
 *
 * Validates open/close behavior, keyboard interaction (Escape),
 * and backdrop click handling. No snapshot tests.
 */
import { render, screen, fireEvent } from '@testing-library/react';
import Modal from '../Modal';

describe('Modal', () => {
    it('renders nothing when isOpen is false', () => {
        const { container } = render(
            <Modal isOpen={false} onClose={jest.fn()} title="Hidden Modal">
                <p>Should not appear</p>
            </Modal>,
        );

        // Grenzwert: isOpen=false → kein DOM-Output
        expect(container.innerHTML).toBe('');
    });

    it('renders title and children when open', () => {
        render(
            <Modal isOpen={true} onClose={jest.fn()} title="Test Title">
                <p>Modal body content</p>
            </Modal>,
        );

        expect(screen.getByText('Test Title')).toBeInTheDocument();
        expect(screen.getByText('Modal body content')).toBeInTheDocument();
    });

    it('calls onClose when Escape key is pressed', () => {
        const onClose = jest.fn();

        render(
            <Modal isOpen={true} onClose={onClose} title="Escape Modal">
                <p>Press Escape</p>
            </Modal>,
        );

        // Grenzwert: Escape-Key muss onClose exakt 1x auslösen
        fireEvent.keyDown(document, { key: 'Escape' });
        expect(onClose).toHaveBeenCalledTimes(1);
    });

    it('calls onClose when backdrop is clicked', () => {
        const onClose = jest.fn();

        render(
            <Modal isOpen={true} onClose={onClose} title="Backdrop Modal">
                <p>Click outside</p>
            </Modal>,
        );

        // Der äussere Container ist der Backdrop-Click-Target
        const backdrop = screen.getByText('Backdrop Modal').closest('.fixed');
        expect(backdrop).not.toBeNull();
        fireEvent.click(backdrop!);
        expect(onClose).toHaveBeenCalledTimes(1);
    });
});
