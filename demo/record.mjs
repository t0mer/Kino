// Records assets/demo/demo.mp4 (+ poster) against a throwaway Kino instance with fake data.
// Usage: node demo/record.mjs   (needs: repo .venv, built web/dist, ffmpeg, `playwright` resolvable
// via NODE_PATH or installed outside the repo; PLAYWRIGHT_CHROMIUM overrides the browser binary.)
import { spawn, execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdtempSync, rmSync, existsSync, statSync, mkdirSync, readdirSync } from 'node:fs';
import { createServer } from 'node:net';
import { tmpdir, homedir } from 'node:os';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomBytes } from 'node:crypto';

const require = createRequire(process.env.PLAYWRIGHT_REQUIRE_FROM || import.meta.url);
const { chromium } = require('playwright');

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const OUT_DIR = join(ROOT, 'assets', 'demo');
const MAX_BYTES = 5 * 1024 * 1024;
const FAKE_PASSWORD = 'demo-password-123'; // throwaway, for the scratch instance only
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function chromiumPath() {
  if (process.env.PLAYWRIGHT_CHROMIUM) return process.env.PLAYWRIGHT_CHROMIUM;
  const base = join(homedir(), '.cache', 'ms-playwright');
  const dir = readdirSync(base).filter((d) => /^chromium-\d+$/.test(d)).sort().pop();
  return join(base, dir, 'chrome-linux64', 'chrome');
}

function freePort() {
  return new Promise((res, rej) => {
    const s = createServer();
    s.listen(0, '127.0.0.1', () => { const { port } = s.address(); s.close(() => res(port)); });
    s.on('error', rej);
  });
}

const work = mkdtempSync(join(tmpdir(), 'kino-demo-'));
let server;
const cleanup = () => {
  if (server && !server.killed) server.kill('SIGTERM');
  rmSync(work, { recursive: true, force: true });
};
process.on('exit', cleanup);
for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => process.exit(1));

const port = await freePort();
const base = `http://127.0.0.1:${port}`;
const env = {
  ...process.env,
  KINO_DATA_DIR: join(work, 'data'),
  KINO_PHOTOS_DIR: join(work, 'data', 'photos'),
  KINO_WEB_DIR: join(ROOT, 'web', 'dist'),
  KINO_WEBHOOK_SECRET: randomBytes(16).toString('hex'),
  KINO_SECRET_KEY: randomBytes(32).toString('hex'),
  KINO_OPENWA_URL: 'http://127.0.0.1:9',
  KINO_OPENWA_API_KEY: 'dummy',
  KINO_OPENWA_SESSION_ID: 'demo-session',
  KINO_ADMIN_USER: 'admin',
};
delete env.KINO_ADMIN_PASSWORD_HASH;
mkdirSync(env.KINO_DATA_DIR, { recursive: true });
const kino = join(ROOT, '.venv', 'bin', 'kino');
execFileSync(kino, ['migrate'], { env, stdio: 'inherit' });
server = spawn(kino, ['api', '--host', '127.0.0.1', '--port', String(port)], { env, stdio: 'ignore' });
for (let i = 0; ; i++) {
  try { if ((await fetch(`${base}/healthz`)).ok) break; } catch { /* not up yet */ }
  if (i > 100) throw new Error('scratch server did not start');
  await sleep(300);
}

const cursorAndCaption = () => {
  const mk = () => {
    if (document.getElementById('__cur')) return;
    const st = document.createElement('style');
    st.textContent = `#__cur{position:fixed;left:0;top:0;width:18px;height:18px;margin:-9px 0 0 -9px;border-radius:50%;
      background:rgba(225,29,72,.85);border:2px solid #fff;box-shadow:0 0 6px rgba(0,0,0,.4);z-index:2147483647;
      pointer-events:none;transition:width .12s,height .12s,margin .12s;box-sizing:border-box}
      #__cur.down{width:26px;height:26px;margin:-13px 0 0 -13px}
      #__cap{position:fixed;left:50%;bottom:12px;transform:translateX(-50%);max-width:1080px;text-align:center;
      background:rgba(15,23,42,.9);color:#fff;font:600 20px system-ui,sans-serif;padding:10px 18px;border-radius:999px;
      z-index:2147483646;pointer-events:none;display:none;line-height:1.3}`;
    document.documentElement.appendChild(st);
    const c = document.createElement('div'); c.id = '__cur'; document.documentElement.appendChild(c);
    const k = document.createElement('div'); k.id = '__cap'; document.documentElement.appendChild(k);
    window.addEventListener('mousemove', (e) => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; }, true);
    window.addEventListener('mousedown', () => c.classList.add('down'), true);
    window.addEventListener('mouseup', () => c.classList.remove('down'), true);
  };
  window.__caption = (t) => {
    mk();
    const k = document.getElementById('__cap');
    k.textContent = t || ''; k.style.display = t ? 'block' : 'none';
  };
  document.addEventListener('DOMContentLoaded', mk);
};

const browser = await chromium.launch({ executablePath: chromiumPath(), args: ['--no-sandbox'] });
const t0 = Date.now();
const ctx = await browser.newContext({
  viewport: { width: 1280, height: 720 },
  recordVideo: { dir: join(work, 'video'), size: { width: 1280, height: 720 } },
  colorScheme: 'light',
});
await ctx.addInitScript(cursorAndCaption);
const page = await ctx.newPage();
let mx = 640, my = 360;
await page.mouse.move(mx, my);

const caption = (t) => page.evaluate((x) => window.__caption(x), t);
async function click(loc) {
  await loc.waitFor({ state: 'visible' });
  await loc.scrollIntoViewIfNeeded();
  const b = await loc.boundingBox();
  const x = b.x + b.width / 2, y = b.y + b.height / 2;
  await page.mouse.move(x, y, { steps: 25 });
  await sleep(250);
  await page.mouse.down(); await sleep(120); await page.mouse.up();
  mx = x; my = y;
}
async function type(loc, text) {
  await click(loc);
  await page.keyboard.type(text, { delay: 70 });
}

const startedAt = Date.now();
await page.goto(base + '/');
await page.getByText('Welcome to Kino').waitFor();
await caption('First run: Kino asks you to choose an admin password, from your local network only.');
await sleep(2300);

await type(page.getByLabel('New password'), FAKE_PASSWORD);
await type(page.getByLabel('Confirm password'), FAKE_PASSWORD);
await caption('The form checks the length and that both passwords match before you can continue.');
await sleep(2200);
await click(page.getByRole('button', { name: 'Set password and sign in' }));
await page.getByRole('navigation', { name: 'Main' }).waitFor();
await caption('Setting the password signs you in straight away, with the full menu on the left.');
await sleep(2300);
await click(page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Kids' }));
await sleep(1200);

await caption('Add your kids one by one. Names can be in any language, and the folder name is generated for you.');
await sleep(2000);
const kids = [['Noa', '2016'], ['Itai', '2018'], ['Maya', '2020']];
for (const [i, [name, year]] of kids.entries()) {
  await click(page.getByRole('button', { name: 'Add kid' }).first());
  const dlg = page.getByRole('dialog');
  await type(dlg.getByLabel('Display name'), name);
  await type(dlg.getByLabel('Birth year'), year);
  await sleep(900);
  await click(dlg.getByRole('button', { name: 'Add kid' }));
  await page.getByRole('heading', { name }).waitFor();
  await sleep(i === 2 ? 1500 : 1000);
}
await caption('Each kid gets a card with a coverage meter that shows how many reference faces are still missing.');
await sleep(3000);

await caption('Auto-save starts off: until you trust the matches, everything goes to review instead.');
await sleep(2500);
await click(page.getByRole('switch').first());
await sleep(1500);
await caption('Switch auto-save on per kid, once the Test page shows the results look right.');
await sleep(2300);

await click(page.getByRole('button', { name: 'Edit Itai' }));
const dlg = page.getByRole('dialog');
await dlg.getByLabel('Birth year').fill('');
await type(dlg.getByLabel('Birth year'), '2017');
await caption('Edit a kid at any time: name, birth year, groups, and a per-kid threshold offset.');
await sleep(2500);
await click(dlg.getByRole('button', { name: 'Save' }));
await sleep(2500);

await caption('The rest of the menu is on its way: review queue, groups, photos, activity, settings and more.');
await sleep(1500);
await click(page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Review' }));
await sleep(1800);
await click(page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Settings' }));
await sleep(2500);
await click(page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Kids' }));
await sleep(1500);

await caption('Kino follows your system theme, and the toggle in the corner switches to dark mode.');
await sleep(2000);
const toggle = page.getByRole('button', { name: /theme \(switch to/i });
for (let i = 0; i < 4; i++) {
  if (/^Dark theme/.test((await toggle.getAttribute('aria-label')) || '')) break;
  await click(toggle);
  await sleep(1000);
}
await sleep(1500);
await caption('Same app in dark mode. Coverage, auto-save and the kid cards all follow the theme.');
await sleep(3000);
await click(page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Review' }));
await sleep(2000);
await click(page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Kids' }));
await sleep(1500);
await caption("That's the tour so far. Thanks for watching!");
await sleep(3000); // hold last frame

const videoPath = await page.video().path();
await ctx.close();
await browser.close();
server.kill('SIGTERM');

const trim = Math.max(0, (startedAt - t0) / 1000 - 0.3);
const raw = videoPath;
const dur = parseFloat(execFileSync('ffprobe', ['-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', raw]).toString());
const outLen = dur - trim;
mkdirSync(OUT_DIR, { recursive: true });
const mp4 = join(OUT_DIR, 'demo.mp4');
execFileSync('ffmpeg', ['-y', '-v', 'error', '-ss', String(trim), '-i', raw,
  '-vf', `fade=t=out:st=${(outLen - 1.5).toFixed(2)}:d=1.5`,
  '-c:v', 'libx264', '-crf', '24', '-preset', 'slow', '-pix_fmt', 'yuv420p',
  '-movflags', '+faststart', '-r', '30', '-an', mp4], { stdio: 'inherit' });
// poster: a Kids-page frame from the light segment, before the dark-mode switch
execFileSync('ffmpeg', ['-y', '-v', 'error', '-ss', process.env.POSTER_AT || '58', '-i', mp4,
  '-frames:v', '1', join(OUT_DIR, 'demo-poster.png')], { stdio: 'inherit' });

const size = statSync(mp4).size;
console.log(`demo.mp4: ${(size / 1024).toFixed(0)} KiB, ${outLen.toFixed(1)} s`);
if (size > MAX_BYTES) { console.error('demo.mp4 exceeds 5 MB'); process.exit(1); }
