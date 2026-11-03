import { fireEvent, render, screen } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import AuthModal from '../AuthModal';
import de from '../../../messages/de.json';
import en from '../../../messages/en.json';
import fr from '../../../messages/fr.json';
import it from '../../../messages/it.json';
jest.unmock('next-intl');
jest.mock('@/contexts/AuthContext', () => ({ useAuth: () => ({ login: jest.fn() }) }));
test.each([
  ['de', de],
  ['en', en],
  ['fr', fr],
  ['it', it],
] as const)('preserves request-access-only login in %s', (locale, messages) => {
  const page = render(
    <NextIntlClientProvider locale={locale} messages={messages}>
      <AuthModal isOpen onClose={jest.fn()} />
    </NextIntlClientProvider>
  );
  expect(screen.getByRole('link', { name: messages.Auth.requestAccess })).toHaveAttribute(
    'href',
    `/${locale}/early-access`
  );
  expect(page.container.querySelectorAll('input')).toHaveLength(2);
  const email = page.container.querySelector('input[type=email]')!,
    password = page.container.querySelector('input[type=password]')!;
  fireEvent.change(email, { target: { value: 'fixture@example.com' } });
  fireEvent.change(password, { target: { value: 'abc' } });
  expect(screen.getByRole('button', { name: messages.Auth.signIn })).toBeEnabled();
  expect(password).not.toHaveAttribute('minLength');
});
