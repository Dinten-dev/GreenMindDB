import React from 'react';
import fs from 'fs';
import path from 'path';
import ts from 'typescript';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { NextIntlClientProvider, createTranslator } from 'next-intl';
import AppLayout from '@/app/[locale]/app/layout';
import AccountPage from '@/app/[locale]/app/account/page';
import SensorCard from '@/app/[locale]/app/sensors/SensorCard';
import { ApiError, apiUpdateMe } from '@/lib/api';
import { useAppText } from '../app-text';
import messageKeys from '../app-message-keys.json';
import de from '../../../messages/de.json';
import en from '../../../messages/en.json';
import fr from '../../../messages/fr.json';
import italian from '../../../messages/it.json';

jest.unmock('next-intl');
const mockPush = jest.fn();
const mockReplace = jest.fn();
let mockLocale = 'de';
jest.mock('next/navigation', () => ({
  usePathname: () => `/${mockLocale}/app/account`,
  useRouter: () => ({ push: mockPush, replace: mockReplace }),
}));
const mockUser = {
  id: 'synthetic',
  name: 'Account',
  email: 'locale@example.invalid',
  role: 'member',
  organization_id: 'org',
  organization_name: 'Greenhouse',
  is_active: true,
  phone_number: null,
};
jest.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({
    user: mockUser,
    loading: false,
    status: 'authenticated',
    issue: null,
    refresh: jest.fn(),
    logout: jest.fn(),
  }),
}));
jest.mock('@/lib/administration-api', () => ({
  adminCapabilities: async () => ({ can_manage: false }),
}));
jest.mock('@/lib/api', () => ({ ...jest.requireActual('@/lib/api'), apiUpdateMe: jest.fn() }));
const catalogs = { de, en, fr, it: italian };
type Locale = keyof typeof catalogs;
const expected: Record<
  Locale,
  {
    account: string;
    overview: string;
    language: string;
    member: string;
    save: string;
    saved: string;
    forbidden: string;
    status: string;
  }
> = {
  de: {
    account: 'Mein Konto',
    overview: 'Überblick',
    language: 'Sprache',
    member: 'Mitglied',
    save: 'Speichern',
    saved: 'Profil gespeichert.',
    forbidden: 'Keine Berechtigung für diesen Vorgang.',
    status: 'Empfängt Daten',
  },
  en: {
    account: 'Account',
    overview: 'Overview',
    language: 'Language',
    member: 'Member',
    save: 'Save',
    saved: 'Profile updated.',
    forbidden: 'You do not have permission for this operation.',
    status: 'Receiving data',
  },
  fr: {
    account: 'Compte',
    overview: 'Vue d’ensemble',
    language: 'Langue',
    member: 'Membre',
    save: 'Enregistrer',
    saved: 'Profil mis à jour.',
    forbidden: 'Vous n’avez pas l’autorisation d’effectuer cette opération.',
    status: 'Réception de données',
  },
  it: {
    account: 'Account',
    overview: 'Panoramica',
    language: 'Lingua',
    member: 'Membro',
    save: 'Salva',
    saved: 'Profilo aggiornato.',
    forbidden: 'Non hai l’autorizzazione per questa operazione.',
    status: 'Ricezione dati',
  },
};
function provider(locale: Locale, children: React.ReactNode) {
  return (
    <NextIntlClientProvider locale={locale} messages={catalogs[locale]} timeZone="UTC">
      {children}
    </NextIntlClientProvider>
  );
}
beforeEach(() => {
  jest.clearAllMocks();
  window.history.replaceState({}, '', '/de/app/account');
  jest.mocked(apiUpdateMe).mockResolvedValue(mockUser);
});

it.each(Object.keys(catalogs) as Locale[])(
  'renders authenticated navigation, account and permission errors in %s',
  async (locale) => {
    mockLocale = locale;
    const labels = expected[locale];
    render(
      provider(
        locale,
        <AppLayout>
          <AccountPage />
        </AppLayout>
      )
    );
    expect(screen.getByRole('heading', { name: labels.account })).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: labels.overview })[0]).toHaveAttribute(
      'href',
      `/${locale}/app/dashboard`
    );
    expect(screen.getByText(labels.member)).toBeInTheDocument();
    // User-entered names stay intact, even when matching a source message.
    expect(screen.getAllByText('Account').length).toBeGreaterThan(0);
    expect(screen.getByText('Greenhouse')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: labels.save }));
    expect(await screen.findByRole('status')).toHaveTextContent(labels.saved);
    jest
      .mocked(apiUpdateMe)
      .mockRejectedValueOnce(new ApiError(403, 'untranslated private server detail'));
    fireEvent.click(screen.getByRole('button', { name: labels.save }));
    expect(await screen.findByRole('alert')).toHaveTextContent(labels.forbidden);
    expect(screen.queryByText('untranslated private server detail')).not.toBeInTheDocument();
  }
);

it.each(Object.keys(catalogs) as Locale[])(
  'preserves localized device status without translating it twice in %s',
  (locale) => {
    mockLocale = locale;
    function Probe() {
      const tx = useAppText();
      return (
        <SensorCard
          name="Biolingo fixture"
          connection="Direct-to-Cloud"
          status={tx('Empfängt Daten')}
          online
          lastSeen={null}
          expanded={false}
          onToggle={() => {}}
        >
          <p>fixture</p>
        </SensorCard>
      );
    }
    render(provider(locale, <Probe />));
    expect(screen.getByText(expected[locale].status)).toBeInTheDocument();
  }
);

it('switches language while retaining the page, device, time range and chart anchor', async () => {
  mockLocale = 'fr';
  window.history.replaceState({}, '', '/fr/app/account?sensor=synthetic&range=7d#chart');
  render(
    provider(
      'fr',
      <AppLayout>
        <AccountPage />
      </AppLayout>
    )
  );
  fireEvent.change(screen.getAllByLabelText('Langue')[0], { target: { value: 'it' } });
  await waitFor(() =>
    expect(mockPush).toHaveBeenCalledWith('/it/app/account?sensor=synthetic&range=7d#chart')
  );
  expect(mockReplace).not.toHaveBeenCalled();
});

it('formats every application message in all four languages without missing keys or broken placeholders', () => {
  const keySet = Object.keys(de.App).sort();
  for (const [locale, messages] of Object.entries(catalogs)) {
    expect(Object.keys(messages.App).sort()).toEqual(keySet);
    const translate = createTranslator({
      locale,
      messages,
      namespace: 'App',
      onError: (error) => {
        throw error;
      },
    });
    for (const key of keySet)
      expect(
        translate(key as keyof typeof de.App, {
          count: 2,
          value0: 'fixture',
          value1: 'fixture',
          value2: 'fixture',
        })
      ).not.toBe(key);
    for (const key of Object.values(messageKeys)) expect(messages.App).toHaveProperty(key);
  }
});

it('keeps fixed application text and visible static labels registered for translation', () => {
  const keys: Record<string, string> = messageKeys;
  const folders = [path.join(process.cwd(), 'src/app/[locale]/app')];
  const files: string[] = [];
  const walk = (folder: string) => {
    for (const entry of fs.readdirSync(folder, { withFileTypes: true })) {
      if (entry.name === '__tests__') continue;
      const filename = path.join(folder, entry.name);
      if (entry.isDirectory()) walk(filename);
      else if (filename.endsWith('.tsx')) files.push(filename);
    }
  };
  folders.forEach(walk);
  const failures: string[] = [];
  for (const filename of files) {
    const source = ts.createSourceFile(
      filename,
      fs.readFileSync(filename, 'utf8'),
      ts.ScriptTarget.Latest,
      true,
      ts.ScriptKind.TSX
    );
    const visit = (node: ts.Node) => {
      if (ts.isJsxText(node) && /[A-Za-zÄäÖöÜü]/.test(node.text))
        failures.push(`${filename}: untranslated JSX text`);
      if (
        ts.isCallExpression(node) &&
        node.expression.getText(source) === 'tx' &&
        node.arguments[0] &&
        ts.isStringLiteral(node.arguments[0]) &&
        !keys[node.arguments[0].text]
      )
        failures.push(`${filename}: ${node.arguments[0].text}`);
      if (
        ts.isPropertyAssignment(node) &&
        ['label', 'description'].includes(node.name.getText(source)) &&
        ts.isStringLiteral(node.initializer) &&
        !keys[node.initializer.text]
      )
        failures.push(`${filename}: ${node.initializer.text}`);
      ts.forEachChild(node, visit);
    };
    visit(source);
  }
  expect(failures).toEqual([]);
});
