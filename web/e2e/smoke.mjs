// Real-backend end-to-end smoke of the Kino web UI. Throwaway instance, dead OpenWA address, no network use.
// Usage: node web/e2e/smoke.mjs
// Needs: repo .venv, built web/dist, `playwright` resolvable via PLAYWRIGHT_REQUIRE_FROM (a path to a
// package.json/dir outside the repo), E2E_MODELS_DIR (dir holding det_10g.onnx + glintr100.onnx) and
// E2E_FACES_DIR (subdirs a/ and b/ with several face photos of two different people, >= 250px).
// Optional: PLAYWRIGHT_CHROMIUM, E2E_KEEP=1 (keep temp dir), E2E_OUT (dir for failure screenshots).
// Without the models/faces the face steps are reported as SKIP.
import { spawn, execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdtempSync, rmSync, mkdirSync, readdirSync, copyFileSync, existsSync, readFileSync, createWriteStream } from 'node:fs';
import { createServer } from 'node:net';
import { tmpdir, homedir } from 'node:os';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomBytes, createHmac } from 'node:crypto';

const require = createRequire(process.env.PLAYWRIGHT_REQUIRE_FROM || import.meta.url);
const { chromium } = require('playwright');

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const PASSWORD = 'smoke-password-123'; // throwaway
const SESSION = 'smoke-session';
const SECRET = randomBytes(16).toString('hex'); // 32 chars
const GROUP = '120363000000000001@g.us';
const OUT = process.env.E2E_OUT || join(tmpdir(), 'kino-e2e-out');
mkdirSync(OUT, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const MODELS = process.env.E2E_MODELS_DIR;
const FACES = process.env.E2E_FACES_DIR;
const haveFaces = !!(MODELS && FACES && existsSync(join(MODELS, 'det_10g.onnx')) &&
  existsSync(join(MODELS, 'glintr100.onnx')) && existsSync(join(FACES, 'a')) && existsSync(join(FACES, 'b')));
const facesOf = (d) => readdirSync(join(FACES, d)).filter((f) => /\.jpe?g$/i.test(f)).sort().map((f) => join(FACES, d, f));

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

const work = mkdtempSync(join(tmpdir(), 'kino-e2e-'));
const procs = [];
const cleanup = () => {
  // SIGKILL: a graceful uvicorn/huey shutdown can outlive this script; the data dir is throwaway anyway
  for (const p of procs) { try { process.kill(-p.pid, 'SIGKILL'); } catch { p.kill('SIGKILL'); } }
  if (!process.env.E2E_KEEP) rmSync(work, { recursive: true, force: true });
};
process.on('exit', cleanup);
for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => process.exit(1));

const port = await freePort();
const base = `http://127.0.0.1:${port}`;
const dataDir = join(work, 'data');
mkdirSync(join(dataDir, 'models'), { recursive: true });
if (haveFaces) for (const f of ['det_10g.onnx', 'glintr100.onnx']) copyFileSync(join(MODELS, f), join(dataDir, 'models', f));
const env = {
  ...process.env,
  KINO_DATA_DIR: dataDir,
  KINO_PHOTOS_DIR: join(dataDir, 'photos'),
  KINO_WEB_DIR: join(ROOT, 'web', 'dist'),
  KINO_WEBHOOK_SECRET: SECRET,
  KINO_SECRET_KEY: randomBytes(32).toString('hex'),
  KINO_OPENWA_URL: 'http://127.0.0.1:9', // dead on purpose
  KINO_OPENWA_API_KEY: 'dummy',
  KINO_OPENWA_SESSION_ID: SESSION,
  KINO_ADMIN_USER: 'admin',
  KINO_DIGEST_CRON: '',
  KINO_WORKERS: '1',
};
delete env.KINO_ADMIN_PASSWORD_HASH;
const kino = join(ROOT, '.venv', 'bin', 'kino');
execFileSync(kino, ['migrate'], { env, stdio: 'inherit' });
const apiLog = join(work, 'api.log');
const spawnLogged = (args, name) => {
  const out = createWriteStream(join(work, name));
  const p = spawn(kino, args, { env, stdio: ['ignore', 'pipe', 'pipe'], detached: true }); // own process group
  p.stdout.pipe(out); p.stderr.pipe(out);
  procs.push(p);
  return p;
};
spawnLogged(['api', '--host', '127.0.0.1', '--port', String(port)], 'api.log');
for (let i = 0; ; i++) {
  try { if ((await fetch(`${base}/healthz`)).ok) break; } catch { /* not up yet */ }
  if (i > 100) throw new Error('scratch server did not start');
  await sleep(300);
}
if (haveFaces) spawnLogged(['worker', '-w', '1'], 'worker.log');

// ---------- harness ----------
const results = [];
const problems = []; // console errors, CSP, 4xx/5xx
let currentPage = 'start';
let step = 0;
const browser = await chromium.launch({ executablePath: chromiumPath(), args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, colorScheme: 'light' });
await ctx.addInitScript(() => {
  document.addEventListener('securitypolicyviolation', (e) =>
    console.error(`CSP violation: ${e.violatedDirective} ${e.blockedURI}`));
});
const page = await ctx.newPage();
page.on('console', (m) => {
  if (['error', 'warning'].includes(m.type())) problems.push(`[${currentPage}] console.${m.type()}: ${m.text().slice(0, 300)}`);
});
page.on('pageerror', (e) => problems.push(`[${currentPage}] pageerror: ${String(e).slice(0, 300)}`));
page.on('response', (r) => {
  if (r.status() >= 400 && r.url().startsWith(base)) problems.push(`[${currentPage}] HTTP ${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`);
});

async function run(name, fn) {
  step++;
  currentPage = name;
  try {
    const note = await fn();
    results.push({ name, ok: true, note: note || '' });
    console.log(`PASS  ${name}${note ? ' - ' + note : ''}`);
  } catch (e) {
    const shot = join(OUT, `fail-${String(step).padStart(2, '0')}.png`);
    await page.screenshot({ path: shot, fullPage: true }).catch(() => {});
    results.push({ name, ok: false, note: String(e.message || e).split('\n').slice(0, 4).join(' | ') });
    console.log(`FAIL  ${name} - ${String(e.message || e).split('\n')[0]} (${shot})`);
  }
}
const skip = (name, why) => { results.push({ name, ok: null, note: why }); console.log(`SKIP  ${name} - ${why}`); };
const nav = (label) => page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: label, exact: true }).click();
const getJson = async (path) => (await page.request.get(base + path)).json();
const until = async (fn, ms = 60000, what = 'condition') => {
  const t = Date.now();
  for (;;) {
    const v = await fn();
    if (v) return v;
    if (Date.now() - t > ms) throw new Error(`timed out waiting for ${what}`);
    await sleep(700);
  }
};
function webhook(jpegPath, id) {
  const body = JSON.stringify({
    event: 'message.received', timestamp: new Date().toISOString(), sessionId: SESSION,
    idempotencyKey: `msg_${SESSION}_${id}`, deliveryId: `dlv_${id}`,
    data: {
      id, from: GROUP, to: '9725****123@c.us', author: '9725****456@c.us', body: '', type: 'image',
      timestamp: Math.floor(Date.now() / 1000), isGroup: true, kind: 'group', fromMe: false, chatId: GROUP,
      media: { mimetype: 'image/jpeg', filename: 'photo.jpg', data: readFileSync(jpegPath).toString('base64') },
      contact: { name: 'Dana', pushName: 'Dana K' },
    },
  });
  const sig = createHmac('sha256', SECRET).update(body).digest('hex');
  return fetch(`${base}/api/webhook/openwa`, {
    method: 'POST', body, headers: { 'content-type': 'application/json', 'x-openwa-signature': `sha256=${sig}` },
  });
}

// ---------- flow ----------
await run('setup: first-run admin password', async () => {
  await page.goto(base + '/');
  await page.getByText('Welcome to Kino').waitFor();
  await page.getByLabel('New password').fill(PASSWORD);
  await page.getByLabel('Confirm password').fill(PASSWORD);
  await page.getByRole('button', { name: 'Set password and sign in' }).click();
  await page.getByRole('navigation', { name: 'Main' }).waitFor();
});

await run('login: sign out and back in', async () => {
  await page.context().clearCookies();
  await page.goto(base + '/');
  await page.getByLabel(/username/i).fill('admin');
  await page.getByLabel(/password/i).fill(PASSWORD);
  await page.getByRole('button', { name: /sign in|log in/i }).click();
  await page.getByRole('navigation', { name: 'Main' }).waitFor();
});

await run('kids: create two kids', async () => {
  await nav('Kids');
  for (const name of ['Alma', 'Ben']) {
    await page.getByRole('button', { name: 'Add kid' }).first().click();
    await page.getByLabel('Display name').fill(name);
    await page.getByRole('dialog').getByRole('button', { name: 'Add kid' }).click();
    await page.getByRole('dialog').waitFor({ state: 'hidden' });
    await page.getByText(name, { exact: true }).first().waitFor();
  }
  const kids = await getJson('/api/kids');
  const n = (kids.items ?? kids).length;
  if (n !== 2) throw new Error(`expected 2 kids, API has ${n}`);
});

const enrolled = {};
async function enroll(kidName, files) {
  await nav('Kids');
  const card = page.locator('[class*="card"]', { hasText: kidName }).filter({ has: page.getByRole('link', { name: /enroll/i }) }).last();
  await card.getByRole('link', { name: /enroll/i }).click();
  await page.getByTestId('dropzone').waitFor();
  await page.getByTestId('file-input').setInputFiles(files);
  await until(async () => (await page.getByText('finding faces').count()) === 0 &&
    (await page.getByRole('status').filter({ hasText: 'Finding faces' }).count()) === 0, 90000, 'face detection');
  const boxes = page.getByRole('button', { name: /^Face \d+, quality/ });
  const nBoxes = await boxes.count();
  if (nBoxes < files.length) throw new Error(`only ${nBoxes} selectable face boxes for ${files.length} photos`);
  // select every photo's best box (first)
  for (const f of files) {
    const name = f.split('/').pop();
    await page.getByTestId(`photo-${name}`).getByRole('button', { name: /^Face 1, quality/ }).first().click();
  }
  await page.getByText(`${files.length} faces selected`).waitFor();
  await page.getByRole('button', { name: 'Save as references' }).click();
  await page.getByText('0 faces selected').waitFor({ timeout: 15000 }).catch(() => {});
  await sleep(1500);
  const kids = await getJson('/api/kids');
  const k = (kids.items ?? kids).find((x) => x.display_name === kidName);
  const refs = await getJson(`/api/kids/${k.id}/references`);
  enrolled[kidName] = k.id;
  return `${nBoxes} boxes, ${(refs.items ?? refs).length} references`;
}
if (haveFaces) {
  await run('enroll: Alma (upload, boxes, select, save)', () => enroll('Alma', facesOf('a').slice(0, 8)));
  await run('enroll: Ben (upload, boxes, select, save)', () => enroll('Ben', facesOf('b').slice(0, 8)));
  await run('enroll: coverage updates', async () => {
    const cov = await getJson(`/api/kids/${enrolled.Alma}/coverage`);
    if (!(cov.reference_count > 0 || cov.count > 0 || JSON.stringify(cov).match(/\d/))) throw new Error(JSON.stringify(cov));
    await page.goto(`${base}/kids/${enrolled.Alma}/enroll`);
    await page.getByText(/reference/i).first().waitFor();
    return JSON.stringify(cov).slice(0, 120);
  });
} else for (const n of ['enroll: Alma', 'enroll: Ben', 'enroll: coverage']) skip(n, 'models/faces not available');

let reviewCount = 0;
if (haveFaces) {
  await run('webhook: unwatched group is discovered, not processed', async () => {
    const r = await webhook(facesOf('a')[9], 'WH-UNWATCHED-1');
    if (r.status !== 200) throw new Error(`webhook ${r.status}`);
    await nav('Groups');
    await page.getByTestId(/^group-/).first().waitFor();
  });
  await run('groups: toggle watched and map kid', async () => {
    const card = page.getByTestId(/^group-/).first();
    await card.getByRole('switch').click();
    await page.getByText('Watched', { exact: true }).first().waitFor();
    await card.getByRole('button', { name: 'Alma' }).click();
    await until(async () => (await card.getByRole('button', { name: 'Alma' }).getAttribute('aria-pressed')) === 'true', 8000, 'kid mapping');
    await page.reload();
    const again = page.getByTestId(/^group-/).first();
    if ((await again.getByRole('button', { name: 'Alma' }).getAttribute('aria-pressed')) !== 'true') throw new Error('kid mapping not persisted');
  });
  await run('webhook + worker: processed, lands in review', async () => {
    for (const [i, f] of [facesOf('a')[10], facesOf('a')[11], facesOf('b')[10]].entries()) {
      const r = await webhook(f, `WH-WATCHED-${i}`);
      if (r.status !== 200) throw new Error(`webhook ${r.status}`);
    }
    const q = await until(async () => {
      const r = await getJson('/api/review');
      const items = r.items ?? r;
      return items.length ? items : null;
    }, 120000, 'review items from the worker');
    // the worker handles the three photos in sequence: wait for all of them to settle
    await until(async () => ((await getJson('/api/images')).items ?? []).filter((i) => i.status === 'done').length === 3, 90000, 'all three images processed');
    reviewCount = (await getJson('/api/review')).items.length;
    return `${reviewCount} review item(s)`;
  });
  await run('review: confirm with key Y', async () => {
    await nav('Review');
    await page.getByRole('button', { name: /confirm/i }).first().waitFor();
    await page.keyboard.press('y');
    await until(async () => (await getJson('/api/review')).items.length === reviewCount - 1, 15000, 'queue to shrink by one');
  });
  await run('photos: confirmed photo appears', async () => {
    await nav('Photos');
    await until(async () => ((await getJson('/api/photos')).items ?? []).length > 0, 15000, 'saved photo');
    await page.locator('img').first().waitFor();
  });
  await run('activity: shows outcomes', async () => {
    await nav('Activity');
    await page.getByText(/\d+ faces?/).first().waitFor(); // not 'done': the status <option> matches first
  });
  await run('gallery: references and histogram', async () => {
    await nav('Gallery');
    await page.locator('img').first().waitFor();
  });
} else for (const n of ['webhook/groups/worker', 'review', 'photos', 'activity', 'gallery']) skip(n, 'models/faces not available');

await run('test page: sliders change buckets', async () => {
  await nav('Test');
  const sliders = page.getByRole('slider');
  await sliders.first().waitFor();
  const before = await page.locator('main').innerText();
  await sliders.first().focus();
  for (let i = 0; i < 6; i++) await page.keyboard.press('ArrowLeft');
  await sleep(1500);
  return `${await sliders.count()} sliders; text ${before === (await page.locator('main').innerText()) ? 'unchanged' : 'changed'}`;
});

await run('settings: sections load, thresholds save, chat picker fails gracefully', async () => {
  await nav('Settings');
  for (const id of ['forwarding', 'digest', 'thresholds', 'retention']) await page.getByTestId(`section-${id}`).waitFor();
  // thresholds: change the first numeric field, save, confirm through the API
  const before = JSON.stringify(await getJson('/api/settings/thresholds'));
  const field = page.getByTestId('section-thresholds').locator('input').first();
  const old = await field.inputValue();
  await field.fill(String(Math.round((Number(old) + 0.02) * 100) / 100));
  await page.getByRole('button', { name: 'Save Thresholds' }).click();
  await until(async () => JSON.stringify(await getJson('/api/settings/thresholds')) !== before, 8000, 'thresholds to change');
  // chat picker with OpenWA down: readable error, no internals
  await page.getByRole('button', { name: /^Default destination/ }).click();
  const msg = await page.getByText('Could not load chats').waitFor().then(() => page.locator('[role=dialog]').innerText());
  if (/127\.0\.0\.1|Traceback|httpx|ConnectError|dummy/.test(msg)) throw new Error('unsanitised picker error: ' + msg.replace(/\s+/g, ' '));
  await page.keyboard.press('Escape');
  return `thresholds ${old} -> saved; picker error: ${msg.replace(/\s+/g, ' ').slice(0, 90)}`;
});

await run('who is this: run clustering, grid renders', async () => {
  await nav('Who is this?');
  const run = page.getByRole('button', { name: /find|run|cluster/i }).first();
  await run.waitFor();
  await run.click();
  await sleep(2500);
  await page.locator('main').waitFor();
});

await run('groups: backfill fails gracefully with a sanitised error', async () => {
  await nav('Groups');
  const card = page.getByTestId(/^group-/).first();
  await card.getByRole('button', { name: /^Backfill/ }).click();
  await page.getByRole('button', { name: 'Start backfill' }).click();
  const alert = await until(async () => {
    const a = page.getByRole('alert').filter({ hasText: /./ });
    return (await a.count()) ? (await a.first().innerText()) : null;
  }, 30000, 'backfill error');
  if (/127\.0\.0\.1|Traceback|httpx|ConnectError|dummy/.test(alert)) throw new Error('unsanitised error: ' + alert);
  return alert.replace(/\s+/g, ' ').slice(0, 120);
});

// responsive + theme sweep
const PAGES = ['/review', '/kids', '/gallery', '/groups', '/photos', '/activity', '/test', '/clusters', '/settings'];
for (const scheme of ['light', 'dark']) {
  for (const [w, h] of [[1280, 800], [390, 844]]) {
    await run(`layout ${scheme} ${w}px: no horizontal scroll, no errors`, async () => {
      await page.setViewportSize({ width: w, height: h });
      await page.emulateMedia({ colorScheme: scheme });
      const bad = [];
      for (const p of PAGES) {
        currentPage = `${scheme}-${w}${p}`;
        await page.goto(base + p);
        await page.locator('main').waitFor();
        await sleep(900);
        const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
        if (over > 1) bad.push(`${p} +${over}px`);
        if (scheme === 'dark' && w === 390) await page.screenshot({ path: join(OUT, `m-${p.slice(1)}.png`) });
      }
      if (bad.length) throw new Error('horizontal overflow: ' + bad.join(', '));
    });
  }
}

await browser.close();

// ---------- report ----------
const fivexx = readFileSync(apiLog, 'utf8').split('\n').filter((l) => /\b5\d\d\b.*(GET|POST|PUT|PATCH|DELETE)|Traceback| ERROR /.test(l));
console.log('\n--- console / network problems ---');
for (const p of [...new Set(problems)]) console.log(p);
console.log('\n--- api log errors ---');
for (const l of fivexx.slice(0, 20)) console.log(l.slice(0, 240));
const failed = results.filter((r) => r.ok === false).length;
console.log(`\n${results.filter((r) => r.ok).length} passed, ${failed} failed, ${results.filter((r) => r.ok === null).length} skipped`);
process.exit(failed ? 1 : 0); // explicit: child stdio pipes would otherwise keep node alive
