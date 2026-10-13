// Throwaway Kino instance + fake local OpenWA for the demo recorder. Nothing here talks to a real
// OpenWA, reads the repo .env or touches the docker containers: the instance gets a temp data dir,
// a free port and no KINO_OPENWA_* / KINO_WEBHOOK_SECRET env (the wizard configures OpenWA).
import { spawn, execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync, mkdirSync, readdirSync } from 'node:fs';
import { createServer } from 'node:net';
import { createServer as createHttpServer } from 'node:http';
import { tmpdir, homedir } from 'node:os';
import { join } from 'node:path';
import { randomBytes } from 'node:crypto';

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export function chromiumPath() {
  if (process.env.PLAYWRIGHT_CHROMIUM) return process.env.PLAYWRIGHT_CHROMIUM;
  const base = join(homedir(), '.cache', 'ms-playwright');
  const dir = readdirSync(base).filter((d) => /^chromium-\d+$/.test(d)).sort().pop();
  return join(base, dir, 'chrome-linux64', 'chrome');
}

export const freePort = () => new Promise((res, rej) => {
  const s = createServer();
  s.listen(0, '127.0.0.1', () => { const { port } = s.address(); s.close(() => res(port)); });
  s.on('error', rej);
});

/**
 * Fake OpenWA on 127.0.0.1: one ready session, webhook list/create; everything else 503.
 * Returns { url, key, calls, close }.
 */
export async function startFakeOpenWA({ key, session }) {
  const calls = [];
  const srv = createHttpServer((req, res) => {
    req.resume();
    req.on('end', () => {
      calls.push(`${req.method} ${req.url}`);
      const send = (status, body) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)); };
      if (req.headers['x-api-key'] !== key) return send(401, { message: 'unauthorized' });
      if (req.method === 'GET' && req.url === '/api/sessions') return send(200, [session]);
      if (req.url === `/api/sessions/${session.id}/webhooks`) return req.method === 'GET' ? send(200, []) : send(201, { id: 'wh-demo' });
      return send(503, { message: 'not available in the demo fake' });
    });
  });
  const port = await freePort();
  await new Promise((r) => srv.listen(port, '127.0.0.1', r));
  return { url: `http://127.0.0.1:${port}`, key, calls, close: () => srv.close() };
}

/** Start `kino api` on a free port with a temp data dir. Returns { base, cleanup }. */
export async function startKino(root) {
  const work = mkdtempSync(join(tmpdir(), 'kino-demo-'));
  const procs = [];
  const cleanup = () => {
    for (const p of procs) { try { process.kill(-p.pid, 'SIGKILL'); } catch { p.kill('SIGKILL'); } }
    rmSync(work, { recursive: true, force: true });
  };
  const port = await freePort();
  const base = `http://127.0.0.1:${port}`;
  const env = {
    ...process.env,
    KINO_DATA_DIR: join(work, 'data'),
    KINO_PHOTOS_DIR: join(work, 'data', 'photos'),
    KINO_WEB_DIR: join(root, 'web', 'dist'),
    KINO_SECRET_KEY: randomBytes(32).toString('hex'),
    KINO_ADMIN_USER: 'admin',
    KINO_DIGEST_CRON: '',
  };
  delete env.KINO_ADMIN_PASSWORD_HASH;
  for (const k of ['KINO_WEBHOOK_SECRET', 'KINO_OPENWA_URL', 'KINO_OPENWA_API_KEY', 'KINO_OPENWA_SESSION_ID']) delete env[k];
  mkdirSync(env.KINO_DATA_DIR, { recursive: true });
  const kino = join(root, '.venv', 'bin', 'kino');
  execFileSync(kino, ['migrate'], { env, cwd: work, stdio: 'ignore' });
  procs.push(spawn(kino, ['api', '--host', '127.0.0.1', '--port', String(port)], { env, cwd: work, stdio: 'ignore', detached: true }));
  for (let i = 0; ; i++) {
    try { if ((await fetch(`${base}/healthz`)).ok) break; } catch { /* not up yet */ }
    if (i > 100) { cleanup(); throw new Error('scratch Kino did not start'); }
    await sleep(300);
  }
  return { base, cleanup };
}
