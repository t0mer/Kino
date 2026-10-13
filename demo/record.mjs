// Narrated demo recorder: plays demo/script.json scene by scene against a throwaway Kino instance
// with fake data, and records the screen (captions, cursor and the talking Kino avatar included).
// It does not play audio: the mixing step places each voice clip at its scene start.
//
// Usage:  node demo/record.mjs [--work <dir>]
//   <dir>  (default $DEMO_WORK or demo/work) must hold clips/manifest.json + clips/<id>.env.json
//          from `demo/.venv/bin/python demo/tts.py --out <dir>/clips`.
// Writes: <dir>/raw.webm       1280x720 screen recording (video t=0 = timeline t=0)
//         <dir>/timeline.json  [{ id, start, end, duration }] seconds from the video start
//         <dir>/recording.json lead-in, sync check, console problems, unmocked calls
// Needs:  repo .venv, built web/dist (`cd web && npm run build`), ffmpeg/ffprobe, and `playwright`
//         resolvable via PLAYWRIGHT_REQUIRE_FROM (PLAYWRIGHT_CHROMIUM overrides the browser binary).
//
// Scenes 'setup', 'openwa', 'webhook' and 'kids' run on the real throwaway backend (the OpenWA it
// connects to is a fake local server). The data-heavy pages answer /api from web/e2e/mock-api.mjs.
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { readFileSync, writeFileSync, mkdirSync, copyFileSync, existsSync, rmSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { overlayScript, showPhone, hidePhone } from './lib/overlays.mjs';
import { syncReport } from './lib/sync.mjs';
import { startKino, startFakeOpenWA, chromiumPath, sleep } from './lib/instance.mjs';
import { mockApi, unmocked, OWA_STATUS, SETTINGS, SCORE } from '../web/e2e/mock-api.mjs';

const require = createRequire(process.env.PLAYWRIGHT_REQUIRE_FROM || import.meta.url);
const { chromium } = require('playwright');

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const argv = process.argv.slice(2);
const WORK = resolve(argv.includes('--work') ? argv[argv.indexOf('--work') + 1] : process.env.DEMO_WORK || join(ROOT, 'demo', 'work'));
const FAKE_PASSWORD = 'demo-password-123'; // throwaway, scratch instance only
const OWA_KEY = 'demo-openwa-key'; // throwaway, fake OpenWA only
const SESSION = { id: 'demo-session', name: 'Family phone', status: 'ready', phone: '972500000001' };

// ---------------------------------------------------------------- inputs (fail loudly)
const script = JSON.parse(readFileSync(join(ROOT, 'demo', 'script.json'), 'utf8'));
const manifest = JSON.parse(readFileSync(join(WORK, 'clips', 'manifest.json'), 'utf8'));
const missing = script.filter((s) => !manifest[s.id] || !existsSync(manifest[s.id].env));
if (missing.length) throw new Error(`no clip for script step(s): ${missing.map((s) => s.id).join(', ')} - run demo/tts.py first`);
const STEP = Object.fromEntries(script.map((s) => [s.id, { ...s, ...manifest[s.id], envelope: JSON.parse(readFileSync(manifest[s.id].env, 'utf8')) }]));

// ---------------------------------------------------------------- throwaway backend + fake OpenWA
const owa = await startFakeOpenWA({ key: OWA_KEY, session: SESSION });
const kino = await startKino(ROOT);
const base = kino.base;
process.on('exit', () => { kino.cleanup(); owa.close(); });
for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => process.exit(1));

// ---------------------------------------------------------------- mocked API for the data pages
const mockState = { jobs: true };
const baseMock = mockApi(mockState);
/** Test page: lowering the L auto-save threshold moves review items into the auto-save bucket (1.5 per 0.01). */
function scoreFor(body) {
  const shift = Math.max(-8, Math.min(8, Math.round((0.45 - (body?.thresholds?.L?.auto ?? 0.45)) * 150)));
  const b = SCORE.buckets;
  const moved = shift > 0 ? b.would_review.items.slice(0, shift) : [];
  const back = shift < 0 ? b.would_auto_save.items.slice(shift) : [];
  return {
    ...SCORE,
    buckets: {
      would_auto_save: { count: b.would_auto_save.count + moved.length - back.length, items: [...moved, ...b.would_auto_save.items.slice(0, b.would_auto_save.items.length - back.length)] },
      would_review: { count: b.would_review.count - moved.length + back.length, items: [...back, ...b.would_review.items.slice(moved.length)] },
      would_drop: b.would_drop,
    },
  };
}
const demoMock = async (route) => {
  const req = route.request();
  const p = new URL(req.url()).pathname;
  const json = (body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  // Keep the mocked pages consistent with what the wizard configured a minute earlier.
  if (p === '/api/openwa/setup/status') return json({ ...OWA_STATUS, url: owa.url, session_id: SESSION.id, session_name: SESSION.name });
  if (p === '/api/settings') return json({ ...SETTINGS, forward: { ...SETTINGS.forward, send_caption: false } });
  if (p === '/api/test/score') return json(scoreFor(req.postDataJSON()));
  return baseMock(route);
};
const useMocks = (page) => page.route('**/api/**', demoMock);
const useBackend = (page) => page.unroute('**/api/**', demoMock);

// ---------------------------------------------------------------- browser + overlays
const browser = await chromium.launch({ executablePath: chromiumPath(), args: ['--no-sandbox'] });
const videoDir = join(WORK, 'video-tmp');
mkdirSync(videoDir, { recursive: true });
const ctx = await browser.newContext({
  viewport: { width: 1280, height: 720 },
  recordVideo: { dir: videoDir, size: { width: 1280, height: 720 } },
  colorScheme: 'light',
});
await ctx.addInitScript({ content: overlayScript });
const t0 = Date.now(); // the screencast starts with the page: video t=0 (checked by syncCheck below)
const page = await ctx.newPage();
const now = () => (Date.now() - t0) / 1000;
const problems = [];
page.on('console', (m) => { if (m.type() === 'error') problems.push(`console: ${m.text().slice(0, 200)}`); });
page.on('pageerror', (e) => problems.push(`pageerror: ${String(e).slice(0, 200)}`));
// The phone mock-up frames the app itself; relax only its document's frame-ancestors to 'self'.
await page.route((u) => u.searchParams.has('demo-mobile'), async (route) => {
  const res = await route.fetch();
  const headers = res.headers();
  headers['content-security-policy'] = (headers['content-security-policy'] || '').replace("frame-ancestors 'none'", "frame-ancestors 'self'");
  await route.fulfill({ response: res, headers });
});
await page.mouse.move(640, 360);

// ---------------------------------------------------------------- UI helpers
const PAUSE = 1100; // after each action (guideline: 1-1.5 s)
const RESULT = 2500; // on result screens
async function moveTo(loc) {
  await loc.waitFor({ state: 'visible' });
  await loc.scrollIntoViewIfNeeded();
  const b = await loc.boundingBox();
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 18 });
  await sleep(150);
}
async function click(loc, pause = PAUSE) {
  await moveTo(loc);
  await page.mouse.down(); await sleep(120); await page.mouse.up();
  if (pause) await sleep(pause);
}
async function type(loc, text) {
  await click(loc, 200);
  await page.keyboard.type(text, { delay: 45 });
  await sleep(500);
}
const hover = async (loc, pause = PAUSE) => { await moveTo(loc); await sleep(pause); };
const navLink = (label) => page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: label, exact: true });
async function settle() {
  // no networkidle: the app polls (review count, jobs), so the network never goes quiet
  await page.evaluate(() => Promise.all([...document.images].map((i) => (i.complete ? 0 : new Promise((r) => { i.onload = i.onerror = r; }))))).catch(() => {});
}
async function nav(label, ready) {
  await click(navLink(label), 0);
  if (ready) {
    await page.locator(ready).first().waitFor({ timeout: 15000 }).catch(async (e) => {
      await page.screenshot({ path: join(WORK, 'fail.png') });
      throw e;
    });
  }
  await settle();
  await sleep(PAUSE);
}
async function smoothScroll(loc, block = 'start') {
  await loc.evaluate((e, b) => e.scrollIntoView({ behavior: 'smooth', block: b }), block);
  await sleep(1000);
}

// ---------------------------------------------------------------- scene engine
const timeline = [];
let next = 0;
/** One script step: caption + avatar speak, run the actions, hold until clip + 0.8 s, clear. */
async function scene(id, actions = async () => {}) {
  const step = script[next++];
  if (!step || step.id !== id) throw new Error(`scene '${id}' is out of order with demo/script.json (expected '${step?.id}')`);
  const s = STEP[id];
  await page.evaluate(([cap, env]) => { window.__caption(cap); window.__avatar.speak(env); }, [s.caption, s.envelope]);
  const start = now();
  await actions();
  const left = start + s.duration + 0.8 - now();
  if (left > 0) await sleep(left * 1000);
  await page.evaluate(() => { window.__caption(''); window.__avatar.idle(); });
  const end = now();
  timeline.push({ id, start: +start.toFixed(3), end: +end.toFixed(3), duration: +(end - start).toFixed(3) });
  console.log(`${id.padEnd(11)} ${start.toFixed(2).padStart(7)} -> ${end.toFixed(2).padStart(7)}  (clip ${s.duration.toFixed(2)} s)`);
}

// ---------------------------------------------------------------- scenes
// Intro over the (mocked) Gallery: what Kino produces. Then the models it runs, on Settings.
await useMocks(page);
await page.goto(base + '/gallery');
await page.locator('[aria-label^="Open photo of"]').first().waitFor({ timeout: 15000 });
await settle();
await sleep(600);

await scene('intro', async () => {
  const photos = page.locator('[aria-label^="Open photo of"]');
  await sleep(800);
  await hover(photos.nth(1));
  await hover(photos.nth(2));
  await hover(photos.nth(6));
});

await scene('local', async () => {
  await nav('Settings', '[data-testid=section-openwa]');
  await smoothScroll(page.getByText('AuraFace-v1').first(), 'center');
  await hover(page.getByText('AuraFace-v1').first(), 1500);
  await hover(page.getByText('SCRFD-10GF detector').first(), RESULT);
});

// First run on the real backend.
await useBackend(page);
await page.goto(base + '/');
await page.getByText('Welcome to Kino').waitFor();
await sleep(500);

await scene('setup', async () => {
  await sleep(600);
  await type(page.getByLabel('New password'), FAKE_PASSWORD);
  await type(page.getByLabel('Confirm password'), FAKE_PASSWORD);
  await click(page.getByRole('button', { name: 'Set password and sign in' }), 0);
  await page.getByRole('heading', { name: 'Connect Kino to OpenWA' }).waitFor();
  await sleep(PAUSE);
});

await scene('openwa', async () => {
  await type(page.getByLabel('OpenWA URL', { exact: true }), owa.url);
  await type(page.getByLabel('API key', { exact: true }), OWA_KEY);
  await click(page.getByRole('button', { name: 'Generate' }));
  await click(page.getByRole('button', { name: 'Test and continue' }), 0);
  const radio = page.getByRole('radio', { name: /Family phone/ });
  await radio.waitFor();
  await sleep(600);
  await click(radio);
});

await scene('webhook', async () => {
  await hover(page.getByText(/9725\*\*\*\*001/).first(), 1200);
  await click(page.getByRole('button', { name: 'Use this session' }), 0);
  await page.getByText('Webhook created').waitFor();
  await sleep(RESULT);
  await click(page.getByRole('button', { name: 'Open Kino' }), 0);
  await page.getByRole('navigation', { name: 'Main' }).waitFor();
  await sleep(600);
});
if (!owa.calls.includes(`POST /api/sessions/${SESSION.id}/webhooks`)) throw new Error('the wizard did not register the webhook in the fake OpenWA');

await scene('kids', async () => {
  await nav('Kids', 'button:has-text("Add kid")');
  for (const name of ['Noa', 'Itai']) {
    await click(page.getByRole('button', { name: 'Add kid' }).first(), 400);
    const dlg = page.getByRole('dialog');
    await type(dlg.getByLabel('Display name'), name);
    await click(dlg.getByRole('button', { name: 'Add kid' }), 0);
    await dlg.waitFor({ state: 'hidden' });
    await page.getByRole('heading', { name }).waitFor();
    await sleep(400);
  }
  await hover(page.getByLabel('Coverage for Noa'), 700);
  await hover(page.getByRole('switch').first(), 1100);
});

// Data-heavy pages: mocked API on the same instance (fake kids, groups and generated faces).
// Reload so no query cached from the real backend survives (the mocks add Maya as a third kid).
await useMocks(page);
await page.goto(base + '/review');
await page.locator('img[alt="Face to review"]').waitFor({ timeout: 15000 });
await settle();

await scene('review', async () => {
  await sleep(800);
  await page.addStyleTag({ content: 'img[alt="Full photo"]{max-height:250px !important}' }); // keep the actions above the caption
  await hover(page.getByRole('button', { name: /confirm/i }).first(), 1200);
  await click(page.getByRole('button', { name: 'Bulk' }));
  for (const id of [500, 502]) await click(page.locator(`[aria-label="Select match ${id}"]`), 500);
  await sleep(800);
});

await scene('references', async () => {
  await nav('References', '[aria-label^="Reference "]');
  await hover(page.locator('[aria-label^="Outlier:"]').first(), 1500);
  await hover(page.getByLabel('References by photo month'), RESULT);
});

await scene('gallery', async () => {
  await nav('Gallery', '[aria-label^="Open photo of"]');
  await click(page.getByRole('tab', { name: /^Noa/ }));
  await click(page.locator('[data-testid=shared-badge]').first(), 0);
  await page.locator('[role=dialog] img[alt^="Photo of"]').waitFor();
  await sleep(RESULT);
  await page.keyboard.press('Escape');
  await sleep(600);
});

await scene('groups', async () => {
  await nav('Groups', '[aria-label="Backfill progress"], [role=progressbar]');
  await hover(page.getByRole('switch').first(), 1200);
  await hover(page.getByTestId(/^group-/).first().getByRole('button', { name: 'Itai' }), 1200);
  await hover(page.locator('[aria-label="Backfill progress"], [role=progressbar]').first(), 1800);
});

await scene('activity', async () => {
  await nav('Activity', '[aria-label="Open image 41"]');
  await click(page.locator('[aria-label="Open image 41"]'), 0);
  await page.locator('[data-testid=face-box]').first().waitFor();
  await settle();
  await sleep(RESULT);
  await hover(page.locator('[data-testid=face-box]').first(), 1500);
  await page.keyboard.press('Escape');
  await sleep(600);
});

await scene('test', async () => {
  await nav('Test', '[role=slider]');
  const slider = page.getByRole('slider').first();
  await click(slider, 400);
  for (let i = 0; i < 4; i++) { await page.keyboard.press('ArrowLeft'); await sleep(220); }
  await sleep(900);
  await smoothScroll(page.getByText('Would auto-save').first(), 'center');
  await sleep(1800);
});

await scene('clusters', async () => {
  await nav('Who is this?', '[aria-label^="Open cluster of"]');
  await hover(page.locator('[aria-label^="Open cluster of"]').nth(1), 900);
  await click(page.locator('[aria-label^="Open cluster of"]').first(), 0);
  await page.locator('[aria-label="Faces in this cluster"]').waitFor();
  await settle();
  await sleep(RESULT);
  await page.keyboard.press('Escape');
  await sleep(500);
});

await scene('settings', async () => {
  await nav('Settings', '[data-testid=section-forwarding]');
  await smoothScroll(page.getByTestId('section-forwarding'));
  await click(page.getByRole('switch', { name: 'Send photos with a caption' }), 1500);
  await hover(page.getByTestId('caption-preview'), 1500);
  await smoothScroll(page.getByTestId('section-digest'));
  await sleep(1500);
});

await scene('theme', async () => {
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'smooth' }));
  await sleep(700);
  const toggle = page.getByRole('button', { name: /theme \(switch to/i });
  for (let i = 0; i < 3 && !/^Dark theme/.test((await toggle.getAttribute('aria-label')) || ''); i++) await click(toggle, 700);
  await sleep(300);
  // A phone-width moment: the same app at a real 390px viewport, framed in the middle.
  await page.evaluate(showPhone, `${base}/kids?demo-mobile=1`);
  const phone = page.frameLocator('#__phone iframe');
  await phone.getByRole('button', { name: 'Open menu' }).waitFor({ timeout: 15000 });
  await sleep(800);
  await click(phone.getByRole('button', { name: 'Open menu' }), 1200);
  await click(phone.getByRole('link', { name: 'Gallery', exact: true }), 0);
  await phone.locator('[aria-label^="Open photo of"]').first().waitFor({ timeout: 15000 });
  await sleep(1400);
});

await scene('outro', async () => {
  await page.evaluate(hidePhone);
  await sleep(400);
  await nav('Gallery', '[aria-label^="Open photo of"]');
});

// ---------------------------------------------------------------- finish + checks
const video = page.video();
await ctx.close();
await browser.close();
const raw = join(WORK, 'raw.webm');
copyFileSync(await video.path(), raw);
rmSync(videoDir, { recursive: true, force: true });
const probe = (args) => execFileSync('ffprobe', ['-v', 'error', ...args, raw]).toString().trim();
const duration = parseFloat(probe(['-show_entries', 'format=duration', '-of', 'csv=p=0']));

// Put the timeline on the video clock: the screencast starts a little after `t0`.
const sync = syncReport(raw, timeline);
if (sync.offset === null) throw new Error('no caption found in raw.webm: cannot check the timeline sync');
const shift = Math.abs(sync.offset) > 0.04 ? sync.offset : 0; // > 1 frame
for (const t of timeline) { t.start = +(t.start + shift).toFixed(3); t.end = +(t.end + shift).toFixed(3); t.duration = +(t.end - t.start).toFixed(3); }
if (sync.max_deviation > 0.2) console.warn(`WARNING: caption edges deviate up to ${sync.max_deviation} s from the timeline`);

writeFileSync(join(WORK, 'timeline.json'), JSON.stringify(timeline, null, 2) + '\n');
writeFileSync(join(WORK, 'recording.json'), JSON.stringify({
  video: raw, duration, size: [1280, 720],
  lead_in: timeline[0].start, // silent page-load seconds before the first caption; trim it and subtract from every start/end
  sync: { ...sync, applied_shift: shift }, // max_deviation: worst caption on/off edge vs the timeline
  mobile: 'in-frame: the theme scene shows a 390px viewport of the app in a centred phone frame (iframe), no extra video to stitch',
  unmocked: [...unmocked], problems: [...new Set(problems)],
}, null, 2) + '\n');

const total = timeline[timeline.length - 1].end;
console.log(`raw.webm ${duration.toFixed(2)} s; timeline ${timeline[0].start.toFixed(2)} -> ${total.toFixed(2)} s; sync shift ${shift} s, max edge deviation ${sync.max_deviation} s`);
if (unmocked.size) console.warn('UNMOCKED API calls:', [...unmocked].join(', '));
if (problems.length) console.warn('Console problems:\n' + [...new Set(problems)].join('\n'));
process.exit(0);
