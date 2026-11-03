/** Built production routing check against a disposable loopback-only Next server. */
import { spawn } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';

const port = Number(process.env.GREENMIND_TEST_PORT || 3048);
if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('Invalid test port');
const origin = `http://127.0.0.1:${port}`;
const server = spawn(
  process.execPath,
  [
    resolve('node_modules/next/dist/bin/next'),
    'start',
    '--hostname',
    '127.0.0.1',
    '--port',
    String(port),
  ],
  { stdio: ['ignore', 'pipe', 'pipe'] }
);
let log = '';
for (const stream of [server.stdout, server.stderr])
  stream.on('data', (part) => {
    log = (log + part).slice(-4000);
  });
const request = (path) =>
  fetch(origin + path, { redirect: 'manual', signal: AbortSignal.timeout(5000) });
const assert = (condition, message) => {
  if (!condition) throw new Error(message);
};
const exited = () => server.exitCode !== null || server.signalCode !== null;
let checks = 0;
try {
  const deadline = Date.now() + 20000;
  let ready = false;
  while (Date.now() < deadline) {
    if (exited()) throw new Error(`Test server exited: ${log}`);
    try {
      ready = log.includes('Ready in') && (await request('/de/about')).status === 200;
    } catch {
      /* Wait for startup. */
    }
    if (ready) break;
    await delay(100);
  }
  assert(ready, `Local test server unavailable: ${log}`);
  for (const locale of ['de', 'en', 'fr', 'it']) {
    const messages = JSON.parse(await readFile(resolve(`messages/${locale}.json`), 'utf8'));
    for (const page of ['about', 'contact', 'science', 'observe/synthetic-public-id']) {
      const path = `/${locale}/${page}`;
      const response = await request(path);
      const html = await response.text();
      const title = page.startsWith('observe/')
        ? messages.ObservationMetadata.title
        : messages.Metadata.title;
      assert(response.status === 200, `Unexpected status for ${path}: ${response.status}`);
      assert(
        new RegExp(`<html[^>]*lang="${locale}"`).test(html),
        `Wrong document language for ${path}`
      );
      assert(html.includes(`<title>${title}</title>`), `Wrong title for ${path}`);
      if (page.startsWith('observe/'))
        assert(
          /<meta name="robots" content="noindex, nofollow"/.test(html),
          `Missing noindex for ${path}`
        );
      checks += 1;
    }
    const response = await request(`/observe/synthetic-public-id?lang=${locale}`);
    assert(
      response.status === 307 &&
        response.headers.get('location') === `/${locale}/observe/synthetic-public-id`,
      `Legacy QR redirect mismatch for ${locale}`
    );
    checks += 1;
  }
  console.log(JSON.stringify({ localeRouteChecks: checks, passed: true }));
} finally {
  server.kill('SIGTERM');
  await Promise.race([
    new Promise((done) => {
      if (exited()) done();
      else server.once('exit', done);
    }),
    delay(3000),
  ]);
  if (!exited()) server.kill('SIGKILL');
}
