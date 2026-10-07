import '@testing-library/jest-dom';

// Mock IntersectionObserver for ScrollReveal component
class MockIntersectionObserver implements IntersectionObserver {
  readonly root: Element | null = null;
  readonly rootMargin: string = '';
  readonly thresholds: ReadonlyArray<number> = [];

  constructor(private callback: IntersectionObserverCallback) {
    // Immediately trigger with isIntersecting = true
    setTimeout(() => {
      this.callback([{ isIntersecting: true } as IntersectionObserverEntry], this);
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

// Mock window.matchMedia (not available in jsdom, required by ScrollReveal)
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: jest.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: jest.fn(),
    removeListener: jest.fn(),
    addEventListener: jest.fn(),
    removeEventListener: jest.fn(),
    dispatchEvent: jest.fn(),
  })),
});

// Mock next-intl
jest.mock('next-intl', () => ({
  useTranslations: (namespace: string) => (key: string, values?: Record<string, string>) => {
    if (!['Content', 'Research', 'Metadata'].includes(namespace)) return key;
    const catalog = require('./messages/de.json');
    let text = catalog[namespace][key] ?? key;
    for (const [name, value] of Object.entries(values ?? {}))
      text = text.replace('{' + name + '}', value);
    return text;
  },
  useLocale: () => 'de',
}));
