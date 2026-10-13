// README screenshot generator for the Kino web UI. Every /api call is mocked through Playwright
// routes; all names, groups and faces are fake (faces are generated cartoon SVGs). No backend,
// OpenWA or network is used.
//
// Usage:  PLAYWRIGHT_REQUIRE_FROM=/path/to/dir/with/playwright node web/e2e/screenshots.mjs
// Env:    PLAYWRIGHT_CHROMIUM  chromium binary (default: newest in ~/.cache/ms-playwright)
//         SHOT_OUT             output dir (default: <repo>/assets/screenshots)
//         SKIP_BUILD=1         reuse the existing web/dist instead of running `npm run build`
//         SHOT_ONLY=a,b        only capture these surfaces (e.g. kids,review)
// Output: <surface>-light.png and <surface>-dark.png at 1280x800.
import { spawn, execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdirSync, readdirSync } from 'node:fs';
import { createServer } from 'node:net';
import { homedir } from 'node:os';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { mockApi, unmocked, resetUploads, UPLOAD_FILES } from './mock-api.mjs';

const require = createRequire(process.env.PLAYWRIGHT_REQUIRE_FROM || import.meta.url);
const { chromium } = require('playwright');

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const WEB = join(ROOT, 'web');
const OUT = process.env.SHOT_OUT || join(ROOT, 'assets', 'screenshots');
const ONLY = process.env.SHOT_ONLY ? new Set(process.env.SHOT_ONLY.split(',')) : null;
mkdirSync(OUT, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function chromiumPath() {
  if (process.env.PLAYWRIGHT_CHROMIUM) return process.env.PLAYWRIGHT_CHROMIUM;
  const base = join(homedir(), '.cache', 'ms-playwright');
  const dir = readdirSync(base).filter((d) => /^chromium-\d+$/.test(d)).sort().pop();
  return join(base, dir, 'chrome-linux64', 'chrome');
}
const freePort = () => new Promise((res, rej) => {
  const s = createServer();
  s.listen(0, '127.0.0.1', () => { const { port } = s.address(); s.close(() => res(port)); });
  s.on('error', rej);
});

// ---------------------------------------------------------------- capture
const BASE_PORT = await freePort();
const base = `http://127.0.0.1:${BASE_PORT}`;
if (!process.env.SKIP_BUILD) execFileSync('npm', ['run', 'build'], { cwd: WEB, stdio: 'inherit' });
const preview = spawn(join(WEB, 'node_modules', '.bin', 'vite'), ['preview', '--host', '127.0.0.1', '--port', String(BASE_PORT), '--strictPort'], { cwd: WEB, stdio: 'ignore', detached: true });
const stop = () => { try { process.kill(-preview.pid, 'SIGKILL'); } catch { preview.kill('SIGKILL'); } };
process.on('exit', stop);
for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => process.exit(1));
for (let i = 0; ; i++) {
  try { if ((await fetch(base)).ok) break; } catch { /* not up yet */ }
  if (i > 60) throw new Error('vite preview did not start');
  await sleep(250);
}

const browser = await chromium.launch({ executablePath: chromiumPath() });
const consoleErrors = [];

async function settle(page) {
  await page.waitForLoadState('networkidle').catch(() => {});
  await page.evaluate(() => Promise.all([...document.images].map((i) => i.complete ? 0 : new Promise((r) => { i.onload = i.onerror = r; })))).catch(() => {});
  await sleep(600);
}

/** One surface, both themes. `fn(page)` navigates and brings the UI into the state to capture. */
async function surface(name, fn, state = {}) {
  if (ONLY && !ONLY.has(name)) return;
  for (const scheme of ['light', 'dark']) {
    const ctx = await browser.newContext({ colorScheme: scheme, viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1 });
    await ctx.addCookies([{ name: 'kino_csrf', value: 'shot', url: base }]);
    await ctx.addInitScript((s) => { try { localStorage.setItem('kino.theme', s); } catch { /* ignore */ } }, scheme);
    const page = await ctx.newPage();
    page.on('console', (m) => { if (m.type() === 'error' && !/status of 404/.test(m.text())) consoleErrors.push(`${name}-${scheme}: ${m.text()}`); });
    page.on('pageerror', (e) => consoleErrors.push(`${name}-${scheme}: pageerror ${e.message}`));
    resetUploads();
    await page.route('**/api/**', mockApi(state));
    await fn(page, scheme);
    await settle(page);
    await page.screenshot({ path: join(OUT, `${name}-${scheme}.png`) });
    console.log(`captured ${name}-${scheme}.png`);
    await ctx.close();
  }
}
const open = async (page, path, ready) => { await page.goto(base + path); if (ready) await page.waitForSelector(ready, { timeout: 15000 }); };

await surface('login', (p) => open(p, '/login', '#username'), { anon: true });
await surface('kids', (p) => open(p, '/kids', 'text=Coverage for Noa').catch(() => open(p, '/kids', 'text=Noa')));
await surface('enroll', async (p) => {
  await open(p, '/kids/1/enroll', '[data-testid=dropzone]');
  await p.setInputFiles('[data-testid=file-input]', UPLOAD_FILES.map((f) => ({ name: f.name, mimeType: 'image/svg+xml', buffer: Buffer.from(f.body) })));
  await p.waitForSelector('[aria-label^="Face 1, quality"]');
  await p.click('[aria-label^="Face 1, quality"]');
  await p.getByRole('button', { name: 'Use this face' }).click();
  await p.mouse.move(0, 0);
});
await surface('review', async (p) => {
  await open(p, '/review', 'img[alt="Face to review"]');
  await p.addStyleTag({ content: 'img[alt="Full photo"]{max-height:290px !important}' }); // keep the action buttons inside 1280x800
});
await surface('review-bulk', async (p) => {
  await open(p, '/review', 'img[alt="Face to review"]');
  await p.getByRole('button', { name: 'Bulk' }).click();
  await p.waitForSelector('[aria-label="Select all"]');
  for (const id of [500, 502, 505]) await p.click(`[aria-label="Select match ${id}"]`);
});
await surface('references', async (p) => {
  await open(p, '/references', 'text=Photo dates by month');
  await p.waitForSelector('[aria-label^="Reference "]');
});
await surface('groups', (p) => open(p, '/groups', '[role=progressbar], [aria-label="Backfill progress"]'), { jobs: true });
await surface('gallery', async (p) => {
  await open(p, '/gallery?kid=1', '[aria-label^="Open photo of"]');
  await p.waitForSelector('[data-testid=shared-badge]');
});
await surface('gallery-lightbox', async (p) => {
  await open(p, '/gallery?kid=1', '[aria-label^="Open photo of"]');
  await p.locator('[data-testid=shared-badge]').first().click();
  await p.waitForSelector('[role=dialog] img[alt^="Photo of"]');
});
await surface('activity', (p) => open(p, '/activity', '[aria-label="Open image 41"]'));
await surface('activity-detail', async (p) => {
  await open(p, '/activity', '[aria-label="Open image 41"]');
  await p.click('[aria-label="Open image 41"]');
  await p.waitForSelector('[data-testid=face-box]');
});
await surface('activity-forwards', async (p) => {
  await open(p, '/activity', '[role=tablist]');
  await p.getByRole('tab', { name: /Forwards/ }).click();
  await p.waitForSelector('text=Failed forwards');
  await p.waitForSelector('text=timed out waiting for OpenWA');
});
await surface('test', (p) => open(p, '/test', '[aria-label="Would auto-save"], section[aria-label]'));
await surface('settings-forwarding', async (p) => {
  // the OpenWA card is first on /settings: scroll to the Forwarding card itself
  await open(p, '/settings', 'text=Forwarding');
  await p.evaluate(() => { const h = [...document.querySelectorAll('h2,h3')].find((e) => e.textContent.trim() === 'Forwarding'); h?.scrollIntoView({ block: 'start' }); window.scrollBy(0, -80); });
});
await surface('settings-thresholds', async (p) => {
  await open(p, '/settings', 'text=Forwarding');
  await p.getByRole('heading', { name: /Thresholds/ }).first().scrollIntoViewIfNeeded();
  await p.evaluate(() => { const h = [...document.querySelectorAll('h2,h3')].find((e) => /Thresholds/.test(e.textContent)); h?.scrollIntoView({ block: 'start' }); window.scrollBy(0, -80); });
});
await surface('clusters', (p) => open(p, '/clusters', '[aria-label^="Open cluster of"]'));
await surface('clusters-open', async (p) => {
  await open(p, '/clusters', '[aria-label^="Open cluster of"]');
  await p.click('[aria-label^="Open cluster of"]');
  await p.waitForSelector('[aria-label="Faces in this cluster"]');
});

await surface('openwa-wizard', async (p) => {
  await open(p, '/setup/openwa', 'text=Connect Kino to OpenWA');
  await p.getByLabel('OpenWA URL', { exact: true }).fill('http://openwa:2785');
  await p.getByLabel('API key', { exact: true }).fill('example-api-key');
  await p.getByRole('button', { name: 'Generate' }).click();
  await p.getByRole('button', { name: 'Test and continue' }).click();
  await p.waitForSelector('text=Home phone');
}, { unconfigured: true });
await surface('settings-openwa', (p) => open(p, '/settings', '[data-testid=section-openwa]'));

await browser.close();
stop();
if (unmocked.size) console.warn('UNMOCKED API calls:', [...unmocked].join(', '));
if (consoleErrors.length) console.warn('Console errors:\n' + consoleErrors.join('\n'));
if (unmocked.size) process.exit(1);
