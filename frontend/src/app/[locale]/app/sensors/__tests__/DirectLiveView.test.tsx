import { act, fireEvent, render, screen } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import DirectLiveView from '../DirectLiveView';
import de from '../../../../../../messages/de.json';
import en from '../../../../../../messages/en.json';
import fr from '../../../../../../messages/fr.json';
import it from '../../../../../../messages/it.json';
jest.unmock('next-intl');

const generation = '00000000-0000-4000-8000-000000000001';
let denied = false;
let marked = false;
const calls: { path: string; method: string; body?: string }[] = [];
const start = 1800000000000000;
const catalogs = { de, en, fr, it };
beforeEach(() => {
  jest.useFakeTimers();
  window.history.replaceState({}, '', '/de/app/sensors');
  denied = marked = false;
  calls.length = 0;
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  Object.defineProperty(crypto, 'randomUUID', {
    configurable: true,
    value: jest.fn(() => '00000000-0000-4000-8000-000000000002'),
  });
  global.fetch = jest.fn(async (url, init) => {
    const path = String(url);
    const method = init?.method ?? 'GET';
    expect(path).toMatch(/^\/api\/v1\/direct-ingest\/devices\/device\/live-/);
    expect(path).not.toContain('/api/v1/api/v1');
    calls.push({ path, method, body: init?.body as string | undefined });
    const bad = denied && method === 'GET';
    if (path.includes('live-markers')) marked = true;
    return {
      ok: !bad,
      status: bad ? 403 : 200,
      json: async () =>
        bad
          ? { detail: 'private backend error' }
          : path.includes('live-leases')
            ? { generation, expires_in_ms: 30000 }
            : {
                generation,
                session_id: 'session',
                preview_profile: 'raw400-box32-v1',
                capability: 'ready',
                stale: false,
                window_complete: false,
                reset: !path.includes('generation='),
                cursor: 2,
                server_us: start + 1100000,
                points: [
                  { cursor: 1, frame: 383, samples: 32, recorded_us: start + 957500, mv: 1650 },
                  { cursor: 2, frame: 415, samples: 32, recorded_us: start + 1037500, mv: 1651 },
                ],
                rail: {
                  start_us: start,
                  end_us: start + 1000000,
                  raw_samples: 400,
                  rail_samples: 320,
                  percent: 80,
                  stale: false,
                  complete: false,
                },
                markers: marked
                  ? [
                      {
                        id: 'marker',
                        session_id: 'session',
                        frame: 415,
                        at_us: start + 1037500,
                        clicked_at_us: start + 1100000,
                      },
                    ]
                  : [],
              },
    } as Response;
  });
});
afterEach(() => {
  jest.useRealTimers();
  jest.restoreAllMocks();
});
function view(locale: keyof typeof catalogs = 'de') {
  return render(
    <NextIntlClientProvider locale={locale} messages={catalogs[locale]} timeZone="UTC">
      <DirectLiveView deviceId="device" onHistory={jest.fn()} />
    </NextIntlClientProvider>
  );
}

test('leases once, reads at 500 ms, renews after ten seconds and releases on unmount', async () => {
  const page = view();
  await screen.findByRole('img', { name: 'Live-Vorschau' });
  expect(calls.filter((c) => c.method === 'PUT')).toHaveLength(1);
  expect(calls.filter((c) => c.method === 'GET')).toHaveLength(1);
  await act(async () => {
    await jest.advanceTimersByTimeAsync(1000);
  });
  expect(calls.filter((c) => c.method === 'GET')).toHaveLength(3);
  await act(async () => {
    await jest.advanceTimersByTimeAsync(9000);
  });
  expect(calls.filter((c) => c.method === 'PUT')).toHaveLength(2);
  page.unmount();
  expect(calls.at(-1)?.method).toBe('DELETE');
});

test('shows high rails despite a plausible trace and binds a marker to the received sample', async () => {
  view();
  expect(await screen.findByText(/80 %/)).toBeInTheDocument();
  expect(screen.getByRole('alert')).toHaveTextContent('Elektroden und Referenz prüfen');
  expect(screen.getByText('Livefenster unvollständig')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Mark' }));
  expect(await screen.findByText('Markierung gesetzt.')).toBeInTheDocument();
  const request = calls.find((c) => c.path.includes('live-markers'))!;
  expect(JSON.parse(request.body!)).toEqual({ generation, session_id: 'session', frame: 415 });
  expect(screen.getByText(/drei Minuten sichtbar/)).toBeInTheDocument();
});

test('window changes keep the same viewer lease and request a complete window', async () => {
  view();
  await screen.findByRole('img');
  fireEvent.click(screen.getByRole('button', { name: '3 Minuten' }));
  await act(async () => {
    await jest.advanceTimersByTimeAsync(500);
  });
  expect(calls.filter((c) => c.method === 'PUT')).toHaveLength(1);
  expect(calls.at(-1)?.path).toContain('seconds=180');
  expect(calls.at(-1)?.path).toContain('after=0');
});

test('hidden tabs release demand and never renew or poll in the background', async () => {
  view();
  await screen.findByRole('img');
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
  fireEvent(document, new Event('visibilitychange'));
  expect(calls.at(-1)?.method).toBe('DELETE');
  const count = calls.length;
  await act(async () => {
    await jest.advanceTimersByTimeAsync(30000);
  });
  expect(calls).toHaveLength(count);
  expect(screen.getByText(/Tab ausgeblendet/)).toBeInTheDocument();
});

test('revoked access clears cached live measurements immediately', async () => {
  view();
  await screen.findByRole('img');
  denied = true;
  await act(async () => {
    await jest.advanceTimersByTimeAsync(500);
  });
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
  expect(screen.getByRole('alert')).not.toHaveTextContent('private backend error');
});

test('missing rail measurements are never rendered as zero', async () => {
  const previous = global.fetch;
  global.fetch = jest.fn(async (url, init) => {
    const response = await previous(url, init);
    if ((init?.method ?? 'GET') !== 'GET') return response;
    return {
      ...response,
      json: async () => ({ ...(await response.json()), rail: null }),
    } as Response;
  });
  view();
  expect(await screen.findByText(/Keine Rail-Messung/)).toBeInTheDocument();
  expect(screen.queryByText(/0 %/)).not.toBeInTheDocument();
});

test('a late lease acknowledgement after unmount is released without starting polling', async () => {
  const previous = global.fetch;
  let finish!: (value: Response) => void;
  global.fetch = jest.fn((url, init) =>
    init?.method === 'PUT'
      ? new Promise<Response>((resolve) => {
          finish = resolve;
        })
      : previous(url, init)
  );
  const page = view();
  page.unmount();
  expect(calls.some((call) => call.method === 'DELETE')).toBe(false);
  await act(async () => {
    finish({ ok: true, status: 200, json: async () => ({}) } as Response);
  });
  expect(calls.filter((call) => call.method === 'DELETE')).toHaveLength(1);
  expect(calls.some((call) => call.method === 'GET')).toBe(false);
});

test('restores the selected live window after a language change', async () => {
  window.history.replaceState({}, '', '/it/app/sensors?directRange=live&directLiveWindow=180');
  view('it');
  await screen.findByRole('img');
  expect(screen.getByRole('button', { name: '3 minuti' })).toHaveAttribute('aria-pressed', 'true');
  expect(calls.find((call) => call.method === 'GET')?.path).toContain('seconds=180');
});

test.each([
  ['de', 'Live-Vorschau', 'Rail-Anteil'],
  ['en', 'Live preview', 'Rail percentage'],
  ['fr', 'Aperçu en direct', 'Part de saturation ADC'],
  ['it', 'Anteprima live', 'Percentuale di saturazione ADC'],
] as const)('renders the live controls and diagnostics in %s', async (locale, title, rail) => {
  view(locale);
  await screen.findByRole('img', { name: title });
  expect(screen.getByText(new RegExp('^' + rail + ':'))).toBeInTheDocument();
  expect(screen.queryByText('Hoher Rail-Anteil. Elektroden und Referenz prüfen.')).toBe(
    locale === 'de' ? screen.getByRole('alert') : null
  );
});
