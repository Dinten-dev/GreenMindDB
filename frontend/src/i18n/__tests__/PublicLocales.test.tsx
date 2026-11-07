import fs from 'fs';
import path from 'path';
import ts from 'typescript';
import { fireEvent, render, screen } from '@testing-library/react';
import { NextIntlClientProvider, createTranslator } from 'next-intl';
import AboutPage from '@/app/[locale]/about/page';
import ProductPage from '@/app/[locale]/product/page';
import TechnologyPage from '@/app/[locale]/technology/page';
import SciencePage from '@/app/[locale]/science/page';
import ContactPage from '@/app/[locale]/contact/page';
import EarlyAccessPage from '@/app/[locale]/early-access/page';
import Impressum from '@/app/[locale]/impressum/page';
import EvaluationPage from '@/app/[locale]/observe/[publicId]/page';
import Navbar from '@/components/Navbar';
import AuthModal from '@/components/AuthModal';
import { apiSubmitContact } from '@/lib/api';
import { apiCreateSession, apiGetPlantContext, apiCreateEvaluation } from '@/lib/observe-api';
import { RESEARCH_PAPERS, CATEGORY_OPTIONS } from '@/lib/research-data';
import keys from '../public-message-keys.json';
import de from '../../../messages/de.json';
import en from '../../../messages/en.json';
import fr from '../../../messages/fr.json';
import it from '../../../messages/it.json';

jest.unmock('next-intl');
let mockLocale = 'de',
  mockPath = '/de/about';
let mockUser: { name: string; email: string } | null = null;
const mockPush = jest.fn();
const mockLogin = jest.fn();
jest.mock('next/navigation', () => ({
  usePathname: () => mockPath,
  useParams: () => ({ locale: mockLocale, publicId: 'synthetic-public-id' }),
  useRouter: () => ({ push: mockPush, replace: jest.fn() }),
}));
jest.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({
    user: mockUser,
    loading: false,
    status: mockUser ? 'authenticated' : 'anonymous',
    refresh: jest.fn(),
    logout: jest.fn(),
    login: mockLogin,
  }),
}));
jest.mock('@/lib/api', () => ({ ...jest.requireActual('@/lib/api'), apiSubmitContact: jest.fn() }));
jest.mock('@/lib/observe-api', () => ({
  apiCreateSession: jest.fn(),
  apiGetPlantContext: jest.fn(),
  apiCreateEvaluation: jest.fn(),
}));
const catalogs = { de, en, fr, it };
type Locale = keyof typeof catalogs;
function view(locale: Locale, element: React.ReactNode) {
  mockLocale = locale;
  return render(
    <NextIntlClientProvider locale={locale} messages={catalogs[locale]} timeZone="UTC">
      {element}
    </NextIntlClientProvider>
  );
}
function translate(locale: Locale, source: string, values?: Record<string, string | number>) {
  const t = createTranslator({ locale, messages: catalogs[locale], namespace: 'Public' });
  return t(keys[source as keyof typeof keys] as keyof typeof de.Public, values);
}
beforeEach(() => {
  jest.clearAllMocks();
  mockUser = null;
  window.history.replaceState({}, '', '/de/about');
  sessionStorage.clear();
  jest
    .mocked(apiCreateSession)
    .mockResolvedValue({ session_token: 'synthetic-token', expires_at: '2030-01-01T00:00:00Z' });
  jest
    .mocked(apiGetPlantContext)
    .mockResolvedValue({ name: 'Ficus fixture' } as Awaited<ReturnType<typeof apiGetPlantContext>>);
  jest
    .mocked(apiCreateEvaluation)
    .mockResolvedValue({} as Awaited<ReturnType<typeof apiCreateEvaluation>>);
});

test.each(Object.keys(catalogs) as Locale[])('renders all public content pages in %s', (locale) => {
  for (const [Component, title] of [
    [AboutPage, 'Über GreenMind.'],
    [ProductPage, 'Was GreenMind kann.'],
    [TechnologyPage, 'Die Wissenschaft'],
    [SciencePage, 'Die Forschung hinter'],
    [ContactPage, 'Kontaktieren Sie uns.'],
    [EarlyAccessPage, 'Zugang anfragen'],
    [Impressum, 'Impressum'],
  ] as const) {
    const page = view(locale, <Component />);
    expect(
      screen.getByRole('heading', {
        name: new RegExp(translate(locale, title).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')),
      })
    ).toBeInTheDocument();
    page.unmount();
  }
});
test.each(Object.keys(catalogs) as Locale[])(
  'localizes form errors without changing submitted values in %s',
  async (locale) => {
    jest.mocked(apiSubmitContact).mockRejectedValueOnce(new Error('private backend detail'));
    view(locale, <ContactPage />);
    fireEvent.change(screen.getByPlaceholderText(translate(locale, 'Ihr Name')), {
      target: { value: 'Monstera fixture' },
    });
    fireEvent.change(screen.getByPlaceholderText(translate(locale, 'sie@beispiel.com')), {
      target: { value: 'fixture@example.com' },
    });
    fireEvent.change(screen.getByPlaceholderText(translate(locale, 'Wie können wir helfen?')), {
      target: { value: 'Not translated user message' },
    });
    fireEvent.click(screen.getByRole('button', { name: translate(locale, 'Nachricht senden') }));
    await screen.findByText(
      translate(locale, 'Senden fehlgeschlagen. Bitte später erneut versuchen.')
    );
    expect(apiSubmitContact).toHaveBeenCalledWith({
      name: 'Monstera fixture',
      email: 'fixture@example.com',
      company: '',
      message: 'Not translated user message',
      website: '',
    });
    expect(screen.queryByText('private backend detail')).not.toBeInTheDocument();
  }
);
test.each(Object.keys(catalogs) as Locale[])(
  'keeps original publication titles and localizes research descriptions in %s',
  (locale) => {
    view(locale, <SciencePage />);
    expect(screen.getByRole('heading', { name: RESEARCH_PAPERS[0].title })).toBeInTheDocument();
    expect(
      screen.getByText(translate(locale, RESEARCH_PAPERS[0].greenmindLink))
    ).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText(translate(locale, 'Paper durchsuchen…')), {
      target: { value: 'does-not-exist' },
    });
    expect(screen.getByText(translate(locale, 'Keine Ergebnisse'))).toBeInTheDocument();
  }
);
test.each(Object.keys(catalogs) as Locale[])(
  'runs all six QR rating steps with unchanged enum values in %s',
  async (locale) => {
    mockPath = `/${locale}/observe/synthetic-public-id`;
    view(locale, <EvaluationPage />);
    fireEvent.click(screen.getByRole('button', { name: translate(locale, 'Bewertung starten') }));
    await screen.findByText('Ficus fixture');
    expect(
      screen.getByText(translate(locale, 'Schritt {step} von {total}', { step: 1, total: 6 }))
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: translate(locale, 'Sehr gut') }));
    fireEvent.click(screen.getByRole('button', { name: translate(locale, 'Weiter') }));
    for (const label of ['Sattgrün', 'Fest / gespannt', 'Normal', 'Keine Auffälligkeiten']) {
      fireEvent.click(screen.getByRole('button', { name: new RegExp(translate(locale, label)) }));
      fireEvent.click(screen.getByRole('button', { name: translate(locale, 'Weiter') }));
    }
    fireEvent.click(screen.getByRole('button', { name: new RegExp(translate(locale, 'Optimal')) }));
    fireEvent.click(screen.getByRole('button', { name: translate(locale, 'Bewertung absenden') }));
    await screen.findByText(translate(locale, 'Danke – Bewertung gespeichert'));
    expect(apiCreateSession).toHaveBeenCalledWith('synthetic-public-id');
    expect(apiCreateEvaluation).toHaveBeenCalledWith('synthetic-token', {
      overall_score: 5,
      color_raw: 'saturated_green',
      structure_raw: 'firm_taut',
      growth_raw: 'normal',
      water_raw: 'optimal',
      anomalies_raw: ['none'],
      detail_notes: undefined,
    });
  }
);
test('keeps language selection available to signed-in public visitors and preserves path, query and anchor', () => {
  mockUser = { name: 'Name unchanged', email: 'fixture@example.com' };
  mockPath = '/fr/science';
  window.history.replaceState({}, '', '/fr/science?category=hardware#papers');
  view('fr', <Navbar />);
  fireEvent.change(screen.getByRole('combobox', { name: 'Langue' }), { target: { value: 'it' } });
  expect(mockPush).toHaveBeenCalledWith('/it/science?category=hardware#papers');
  expect(screen.getAllByText('Name unchanged').length).toBeGreaterThan(0);
});
test('formats every public message and keeps research summaries registered', () => {
  for (const [locale, messages] of Object.entries(catalogs)) {
    expect(Object.keys(messages.Public).sort()).toEqual(Object.keys(de.Public).sort());
    const t = createTranslator({
      locale,
      messages,
      namespace: 'Public',
      onError: (error) => {
        throw error;
      },
    });
    for (const key of Object.values(keys))
      expect(
        t(key as keyof typeof de.Public, { count: 2, name: 'fixture', step: 2, total: 6 })
      ).not.toBe(key);
  }
  const registry: Record<string, string> = keys;
  for (const paper of RESEARCH_PAPERS)
    for (const value of [paper.categoryLabel, paper.greenmindLink, paper.abstract])
      expect(registry[value]).toBeDefined();
  for (const category of CATEGORY_OPTIONS) expect(registry[category.label]).toBeDefined();
});
test('guards public JSX, attributes and literal messages against untranslated additions', () => {
  const registry: Record<string, string> = keys,
    failures: string[] = [];
  const sources = [
    'about',
    'product',
    'technology',
    'science',
    'contact',
    'early-access',
    'impressum',
    'observe/[publicId]',
  ].map((page) => `src/app/[locale]/${page}/page.tsx`);
  sources.push(
    'src/components/Header.tsx',
    'src/components/AuthModal.tsx',
    'src/app/ClientLayout.tsx'
  );
  const invariant = (value: string) =>
    !/[A-Za-zÀ-ž]/.test(value) ||
    /^(GreenMind\.?|GreenMind GmbH|Galaxyadvisors AG|Laurenzenvorstadt 69|5000 Aarau|info@green-mind\.ch|DOI:)$/.test(
      value
    );
  for (const file of sources) {
    const source = ts.createSourceFile(
      file,
      fs.readFileSync(path.join(process.cwd(), file), 'utf8'),
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TSX
    );
    function visit(node: ts.Node) {
      if (ts.isJsxText(node) && !invariant(node.text.replace(/\s+/g, ' ').trim()))
        failures.push(`${file}: JSX ${node.text.trim()}`);
      if (
        ts.isJsxAttribute(node) &&
        ['title', 'placeholder', 'aria-label', 'alt', 'subtitle', 'nextLabel'].includes(
          node.name.getText(source)
        ) &&
        node.initializer &&
        ts.isStringLiteral(node.initializer) &&
        !invariant(node.initializer.text)
      )
        failures.push(`${file}: attribute ${node.initializer.text}`);
      if (
        ts.isCallExpression(node) &&
        node.expression.getText(source) === 'tx' &&
        node.arguments[0] &&
        ts.isStringLiteral(node.arguments[0]) &&
        !registry[node.arguments[0].text]
      )
        failures.push(`${file}: message ${node.arguments[0].text}`);
      ts.forEachChild(node, visit);
    }
    visit(source);
  }
  expect(failures).toEqual([]);
});

test.each(Object.keys(catalogs) as Locale[])(
  'localizes legacy login failures in %s',
  async (locale) => {
    mockLogin.mockRejectedValueOnce(new Error('private backend diagnostic'));
    const page = view(locale, <AuthModal isOpen onClose={jest.fn()} />);
    fireEvent.change(page.container.querySelector('input[type=email]')!, {
      target: { value: 'fixture@example.com' },
    });
    fireEvent.change(page.container.querySelector('input[type=password]')!, {
      target: { value: 'TestPass123!' },
    });
    fireEvent.click(screen.getByRole('button', { name: catalogs[locale].Auth.signIn }));
    await screen.findByText(catalogs[locale].Auth.errors.service);
    expect(screen.queryByText('private backend diagnostic')).not.toBeInTheDocument();
  }
);
