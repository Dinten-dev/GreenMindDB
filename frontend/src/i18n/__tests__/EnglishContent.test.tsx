import { render, screen, cleanup, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import TechnologyPage from '@/app/[locale]/technology/page';
import ProductPage from '@/app/[locale]/product/page';
import AboutPage from '@/app/[locale]/about/page';
import SciencePage from '@/app/[locale]/science/page';
import ContactPage from '@/app/[locale]/contact/page';
import EarlyAccessPage from '@/app/[locale]/early-access/page';
import LoginPage from '@/app/[locale]/login/page';
import SignupPage from '@/app/[locale]/signup/page';
import LegalPage from '@/app/[locale]/impressum/page';
import Navbar from '@/components/Navbar';
import de from '../../../messages/de.json';
import en from '../../../messages/en.json';

let mockLocale = 'en';
let mockPathname = '/de/technology';
const mockPush = jest.fn();
jest.mock('next-intl', () => ({
  useLocale: () => mockLocale,
  useTranslations: (namespace: string) => (key: string, values?: Record<string, string>) => {
    const messages = (mockLocale === 'en' ? en : de) as unknown as Record<
      string,
      Record<string, string>
    >;
    let value = messages[namespace][key];
    if (typeof value !== 'string') throw new Error('Missing translation: ' + namespace + '.' + key);
    for (const [name, text] of Object.entries(values ?? {}))
      value = value.replace('{' + name + '}', text);
    return value;
  },
}));
jest.mock('next/navigation', () => ({
  useRouter: () => ({ push: mockPush }),
  usePathname: () => mockPathname,
}));
jest.mock('@/lib/api', () => ({
  apiGetMe: jest.fn().mockResolvedValue(null),
  apiLogin: jest.fn().mockResolvedValue({}),
  apiSubmitEarlyAccess: jest.fn().mockResolvedValue({ status: 'ok' }),
  apiSubmitContact: jest.fn().mockResolvedValue({ status: 'ok' }),
}));
import { apiLogin, apiSubmitEarlyAccess } from '@/lib/api';

beforeEach(() => {
  mockLocale = 'en';
  mockPathname = '/de/technology';
  jest.clearAllMocks();
});
afterEach(cleanup);

it.each([
  [TechnologyPage, 'The science behind GreenMind.'],
  [ProductPage, 'What GreenMind can do.'],
  [AboutPage, 'About GreenMind.'],
  [SciencePage, 'The research behind GreenMind.'],
  [ContactPage, 'Contact us.'],
  [EarlyAccessPage, 'Request access'],
  [LoginPage, 'Sign in to GreenMind'],
  [SignupPage, 'Create an account'],
  [LegalPage, 'Legal notice'],
])('renders translated body content for %p', (Page, title) => {
  render(<Page />);
  expect(screen.getByRole('heading', { level: 1, name: title })).toBeInTheDocument();
});

it('keeps German content when German is selected', () => {
  mockLocale = 'de';
  render(<TechnologyPage />);
  expect(
    screen.getByRole('heading', { level: 1, name: 'Die Wissenschaft hinter GreenMind.' })
  ).toBeInTheDocument();
});

it('uses localized research summaries in search without changing citations', async () => {
  const user = userEvent.setup();
  render(<SciencePage />);
  await user.type(screen.getByPlaceholderText('Search papers…'), 'Flexible organic');
  expect(screen.getByText(/Flexible organic multi-electrode arrays/)).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Read paper →' })).toHaveAttribute(
    'href',
    'https://doi.org/10.1126/sciadv.adh4443'
  );
});

it('preserves the selected language after signing in', async () => {
  const user = userEvent.setup();
  render(<LoginPage />);
  await user.type(screen.getByPlaceholderText('you@example.com'), 'example@example.com');
  await user.type(screen.getByPlaceholderText('Enter your password'), 'test-password-only');
  await user.click(screen.getByRole('button', { name: 'Sign in' }));
  await waitFor(() =>
    expect(apiLogin).toHaveBeenCalledWith('example@example.com', 'test-password-only')
  );
  expect(mockPush).toHaveBeenCalledWith('/en/app/dashboard');
});

it('translates the access form while preserving API country values', async () => {
  const user = userEvent.setup();
  render(<EarlyAccessPage />);
  await user.type(screen.getByPlaceholderText('Your name'), 'Example');
  await user.type(screen.getByPlaceholderText('you@example.com'), 'example@example.com');
  await user.type(screen.getByPlaceholderText('Company or farm'), 'Example farm');
  await user.selectOptions(screen.getByRole('combobox'), 'Schweiz');
  await user.click(screen.getByRole('button', { name: 'Request access' }));
  await waitFor(() =>
    expect(apiSubmitEarlyAccess).toHaveBeenCalledWith(
      expect.objectContaining({ country: 'Schweiz' })
    )
  );
  expect(screen.getByText('Request received!')).toBeInTheDocument();
});

it('preserves page, query and anchor when switching language', async () => {
  const user = userEvent.setup();
  mockLocale = 'de';
  window.history.replaceState(null, '', '/de/technology?section=hardware#hardware');
  render(<Navbar />);
  await user.selectOptions(screen.getByRole('combobox', { name: 'Sprache' }), 'en');
  expect(mockPush).toHaveBeenCalledWith('/en/technology?section=hardware#hardware');
});

it('contains complete matching German and English catalog keys', () => {
  for (const namespace of ['Content', 'Research', 'Metadata'] as const) {
    expect(Object.keys(en[namespace]).sort()).toEqual(Object.keys(de[namespace]).sort());
    expect(
      Object.values(en[namespace]).every(
        (value) => typeof value === 'string' && value.trim().length > 0
      )
    ).toBe(true);
  }
});
