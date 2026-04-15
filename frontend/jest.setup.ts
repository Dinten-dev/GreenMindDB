import '@testing-library/jest-dom';

// Mock IntersectionObserver for ScrollReveal component
class MockIntersectionObserver implements IntersectionObserver {
    readonly root: Element | null = null;
    readonly rootMargin: string = '';
    readonly thresholds: ReadonlyArray<number> = [];

    constructor(private callback: IntersectionObserverCallback) {
        // Immediately trigger with isIntersecting = true
        setTimeout(() => {
            this.callback(
                [{ isIntersecting: true } as IntersectionObserverEntry],
                this,
            );
        }, 0);
    }

    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
    takeRecords(): IntersectionObserverEntry[] {
        return [];
    }
}

Object.defineProperty(window, 'IntersectionObserver', {
    writable: true,
    value: MockIntersectionObserver,
});
