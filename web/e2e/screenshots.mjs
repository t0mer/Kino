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

// ---------------------------------------------------------------- generated cartoon faces
const hash = (s) => { let h = 2166136261; for (const c of String(s)) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619); } return h >>> 0; };
const SKIN = ['#f2c9a5', '#e0a97e', '#c68863', '#8d5a3b', '#f7d7bd'];
const HAIR = ['#2b1b12', '#6b3f1d', '#c9902f', '#141414', '#a0522d'];
const SHIRT = ['#4f7cff', '#e5566d', '#3fb68b', '#f2a93b', '#9b6bd6'];

/** A face + shoulders drawn in a 100x(100+) box; `key` = "<person>-<variant>". */
function person(key, x, y, w) {
  const [who, variant = '0'] = String(key).split('-');
  const h = hash(who);
  const v = hash(variant) % 5;
  const skin = SKIN[h % 5], hair = HAIR[(h >> 3) % 5], shirt = SHIRT[(h >> 6) % 5];
  const long = (h >> 9) % 2 === 0;
  const k = w / 100;
  return `<g transform="translate(${x} ${y}) scale(${k})">
  <path d="M8 130 Q10 92 50 88 Q90 92 92 130 Z" fill="${shirt}"/>
  ${long ? `<path d="M10 52 Q8 8 50 8 Q92 8 90 52 L92 84 Q50 74 8 84 Z" fill="${hair}"/>` : ''}
  <circle cx="50" cy="50" r="38" fill="${skin}"/>
  <path d="M12 46 Q12 12 50 12 Q88 12 88 46 Q72 28 50 30 Q28 28 12 46 Z" fill="${hair}"/>
  <circle cx="37" cy="${50 + (v % 2)}" r="3.6" fill="#222"/><circle cx="63" cy="${50 + (v % 2)}" r="3.6" fill="#222"/>
  <circle cx="30" cy="62" r="5" fill="#ff8a8a" opacity=".35"/><circle cx="70" cy="62" r="5" fill="#ff8a8a" opacity=".35"/>
  <path d="M39 65 Q50 ${72 + v * 2} 61 65" fill="none" stroke="#7a2e2e" stroke-width="3" stroke-linecap="round"/>
</g>`;
}
const svg = (w, h, body) => `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">${body}</svg>`;
/** Square avatar crop. */
function cropSvg(key) {
  const bg = `hsl(${hash(String(key).split('-')[0]) % 360} 55% 86%)`;
  return svg(160, 160, `<rect width="160" height="160" fill="${bg}"/>${person(key, 14, 8, 132)}`);
}
/** Face layout of every generated photo (pixels of a 1200x800 image). */
const LAYOUT = [
  { x: 190, y: 250, w: 150, h: 150 },
  { x: 560, y: 290, w: 130, h: 130 },
  { x: 890, y: 230, w: 96, h: 96 },
];
const PEOPLE = ['noa', 'itai', 'friend'];
function sceneSvg(seed, faces = LAYOUT, people = PEOPLE) {
  const hue = hash(seed) % 360;
  const body = `<defs><linearGradient id="s" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="hsl(${hue} 70% 82%)"/><stop offset="1" stop-color="hsl(${hue} 60% 94%)"/></linearGradient></defs>
<rect width="1200" height="800" fill="url(#s)"/><rect y="560" width="1200" height="240" fill="hsl(${(hue + 100) % 360} 35% 62%)"/>
<circle cx="1050" cy="110" r="60" fill="#ffe27a"/>
${faces.map((f, i) => person(`${people[i % people.length]}-${i}`, f.x, f.y, f.w)).join('')}`;
  return svg(1200, 800, body);
}

// ---------------------------------------------------------------- fixtures (all fake)
const iso = (minsAgo) => new Date(Date.now() - minsAgo * 60_000).toISOString().slice(0, 19);
const day = (daysAgo) => iso(daysAgo * 1440);

const KIDS = [
  { id: 1, display_name: 'Noa', slug: 'noa', birth_year: 2016, active: true, auto_save_enabled: true, threshold_offset: 0, group_ids: [1, 2], reference_count: 14, today: 3, last_7_days: 12, created_at: day(120) },
  { id: 2, display_name: 'Itai', slug: 'itai', birth_year: 2018, active: true, auto_save_enabled: false, threshold_offset: 0.02, group_ids: [1, 3], reference_count: 6, today: 1, last_7_days: 5, created_at: day(120) },
  { id: 3, display_name: 'Maya', slug: 'maya', birth_year: 2020, active: true, auto_save_enabled: true, threshold_offset: -0.01, group_ids: [3, 4], reference_count: 11, today: 0, last_7_days: 4, created_at: day(90) },
];
const GROUPS = [
  { id: 1, chat_id: 'group-1', name: 'Class 3B parents', slug: 'class-3b-parents', watched: true, kid_ids: [1, 2], is_forward_destination: false, created_at: day(100), last_seen_at: iso(25) },
  { id: 2, chat_id: 'group-2', name: 'Football Saturday', slug: 'football-saturday', watched: true, kid_ids: [1], is_forward_destination: false, created_at: day(100), last_seen_at: iso(300) },
  { id: 3, chat_id: 'group-3', name: 'Sunshine kindergarten', slug: 'sunshine-kindergarten', watched: true, kid_ids: [2, 3], is_forward_destination: false, created_at: day(80), last_seen_at: iso(90) },
  { id: 4, chat_id: 'group-4', name: 'Art club', slug: 'art-club', watched: true, kid_ids: [3], is_forward_destination: false, created_at: day(60), last_seen_at: day(2) },
  { id: 5, chat_id: 'group-5', name: 'Neighbours', slug: 'neighbours', watched: false, kid_ids: [], is_forward_destination: false, created_at: day(30), last_seen_at: day(6) },
  { id: 6, chat_id: 'group-6', name: 'Family album', slug: 'family-album', watched: false, kid_ids: [], is_forward_destination: true, created_at: day(30), last_seen_at: day(1) },
];
const coverage = (k) => ({
  kid_id: k.id, count: k.reference_count, recommended: 10,
  yaw_known: k.reference_count, left: Math.round(k.reference_count * 0.2), frontal: Math.round(k.reference_count * 0.6), right: Math.round(k.reference_count * 0.2),
  older_than_year: k.id === 1 ? 4 : 0,
  hints: k.reference_count < 10 ? ['Add more references (recommended minimum is 10).', 'Add side angles.'] : k.id === 1 ? ['Several references are over a year old. Add recent photos.'] : [],
});
const SOURCES = ['upload', 'upload', 'cluster', 'review', 'auto', 'upload', 'cluster', 'review', 'auto', 'upload', 'review', 'upload', 'cluster', 'auto', 'upload', 'review'];
function refsFor(kid) {
  const n = kid.reference_count;
  return Array.from({ length: n }, (_, i) => ({
    id: kid.id * 100 + i, kid_id: kid.id, face_id: 1000 + i, source: SOURCES[i % SOURCES.length], status: 'active',
    quality: Math.round((0.95 - (i % 7) * 0.07) * 100) / 100, photo_date: day(8 + i * (kid.id === 1 ? 38 : 14)),
    added_at: day(5 + i), weight: 0.9 - i * 0.03, yaw: [-30, 0, 4, 28, -8, 0, 12][i % 7],
    crop_url: `/api/crops/${['noa', 'itai', 'maya'][kid.id - 1]}-${i}`,
    ...(kid.id === 1 && (i === 3 || i === 9) ? { outlier: true, outlier_reason: i === 3 ? 'far_from_centroid' : 'closer_to_other_kid' } : {}),
  }));
}
const NAMES = { 1: 'Noa', 2: 'Itai', 3: 'Maya' };
const KEYNAME = { 1: 'noa', 2: 'itai', 3: 'maya' };
const reviewItem = (i, kid, score, tier, method = 'face') => ({
  match_id: 500 + i, face_id: 700 + i, image_id: 300 + i, kid_id: kid, kid_name: NAMES[kid], score, margin: 0.06 + (i % 3) * 0.02, tier, method,
  group_id: 1 + (i % 3), group_name: GROUPS[i % 3].name, sent_at: iso(40 + i * 55),
  bbox: LAYOUT[i % 3], crop_url: `/api/crops/${KEYNAME[kid]}-${i + 20}`, image_url: `/api/images/${300 + i}/file`,
  nearest_refs: [0, 1, 2].map((r) => ({ reference_id: kid * 100 + r, similarity: Math.round((score + 0.12 - r * 0.03) * 100) / 100, crop_url: `/api/crops/${KEYNAME[kid]}-${r}` })),
});
const REVIEW = [
  reviewItem(0, 1, 0.41, 'M'), reviewItem(1, 2, 0.37, 'L'), reviewItem(2, 1, 0.44, 'L'), reviewItem(3, 3, 0.39, 'M'),
  reviewItem(4, 2, 0.34, 'S', 'burst'), reviewItem(5, 1, 0.42, 'M'), reviewItem(6, 3, 0.36, 'L'), reviewItem(7, 2, 0.38, 'M'),
];
const ACT = [
  { id: 41, gi: 0, status: 'done', face_count: 3, matches: { auto_saved: 2, review: 1 }, ms: 1840 },
  { id: 40, gi: 1, status: 'done', face_count: 1, matches: { auto_saved: 1 }, ms: 920 },
  { id: 39, gi: 2, status: 'done', face_count: 6, matches: { review: 2, rejected: 1 }, ms: 2460 },
  { id: 38, gi: 0, status: 'skipped', skip_reason: 'duplicate', face_count: 0, matches: {}, ms: 40 },
  { id: 37, gi: 3, status: 'done', face_count: 2, matches: { auto_saved: 1 }, ms: 1310 },
  { id: 36, gi: 1, status: 'failed', face_count: 0, matches: {}, ms: 5000, error: 'could not decode image' },
  { id: 35, gi: 2, status: 'processing', face_count: 0, matches: {}, ms: null },
  { id: 34, gi: 0, status: 'queued', face_count: 0, matches: {}, ms: null },
].map((a, n) => ({
  id: a.id, group_id: GROUPS[a.gi].id, group_name: GROUPS[a.gi].name, sender: ['Dana', 'Omer', 'Yael', 'Guy'][n % 4], sent_at: iso(10 + n * 70),
  received_at: iso(10 + n * 70), status: a.status, skip_reason: a.skip_reason ?? null, processing_ms: a.ms, error: a.error ?? null,
  face_count: a.face_count, matches: a.matches, has_file: true,
}));
const detail = (id) => ({
  id, group_id: 1, group_name: GROUPS[0].name, sender: 'Dana', sent_at: iso(30), received_at: iso(30), width: 1200, height: 800,
  status: 'done', skip_reason: null, duplicate_of: null, processing_ms: 1840, error: null, has_file: true, file_url: `/api/images/${id}/file`,
  faces: LAYOUT.map((b, i) => ({ id: 900 + i, bbox: [b.x, b.y, b.w, b.h], det_score: 0.9, size_px: b.w, quality: [0.86, 0.71, 0.48][i], tier: ['L', 'L', 'M'][i], from_tile: i === 2, crop_url: `/api/crops/${PEOPLE[i]}-${i}` })),
  matches: [
    { id: 1, face_id: 900, kid_id: 1, kid_name: 'Noa', score: 0.58, margin: 0.21, method: 'face', decision: 'auto_saved', decided_by: 'system' },
    { id: 2, face_id: 901, kid_id: 2, kid_name: 'Itai', score: 0.37, margin: 0.09, method: 'face', decision: 'review', decided_by: 'system' },
  ],
  forwards: [{ id: 12, status: 'sent', attempts: 1, kid_ids: [1], last_error: null, created_at: iso(29), sent_at: iso(28) }],
});
const FWD = (id, img, status, extra = {}) => ({ id, image_id: img, dest_chat_id: 'chat-family', kid_ids: [1], caption: null, status, attempts: 4, last_error: null, created_at: iso(240), sent_at: null, next_attempt_at: null, ...extra });
const FAILED = [
  FWD(21, 33, 'failed', { last_error: 'OpenWA returned HTTP 503: session is reconnecting' }),
  FWD(20, 31, 'failed', { attempts: 4, last_error: 'timed out waiting for OpenWA' }),
];
const PENDING = [FWD(22, 41, 'pending', { attempts: 1, created_at: iso(3), next_attempt_at: new Date(Date.now() + 5 * 60_000).toISOString().slice(0, 19), last_error: 'rate limited, retrying' })];
// Saved photos: one image can be saved for several kids (a shared photo has a row per kid).
const KID_SETS = [[1], [2], [1, 2], [3], [1], [2, 3], [3], [1], [2], [1, 3], [2], [3], [1], [1, 2, 3], [2], [3], [1], [2]];
const SENDERS = ['Dana', 'Omer', 'Yael', 'Guy'];
const IMAGES = KID_SETS.map((kids, i) => ({ i, kids, imageId: 200 + i, sentMins: 90 + i * 2700, gi: i % 4 }));
const photoRow = (im, kidId, listKids) => ({
  id: im.imageId * 10 + kidId, image_id: im.imageId, kid_id: kidId, kid_name: NAMES[kidId], group_id: GROUPS[im.gi].id, group_name: GROUPS[im.gi].name,
  sent_at: iso(im.sentMins), saved_at: iso(im.sentMins - 5), file_url: `/api/images/${im.imageId}/file`, thumb_url: `/api/photos/${im.imageId * 10 + kidId}/thumb`,
  sender_name: SENDERS[im.i % 4], kid_ids: listKids, kid_names: listKids.map((k) => NAMES[k]),
  kids: listKids.map((k) => ({ kid_id: k, kid_name: NAMES[k], saved_photo_id: im.imageId * 10 + k })),
});
/** The "All" tab shows each image once (first kid); a kid tab shows that kid's row. */
const photosFor = (kid) => (kid ? IMAGES.filter((im) => im.kids.includes(kid)).map((im) => photoRow(im, kid, im.kids)) : IMAGES.map((im) => photoRow(im, im.kids[0], im.kids)));
const PHOTO_COUNTS = { total: IMAGES.length, kids: [1, 2, 3].map((k) => ({ kid_id: k, kid_name: NAMES[k], count: IMAGES.filter((im) => im.kids.includes(k)).length })) };
const PHOTO_PEOPLE = { 1: 'noa', 2: 'itai', 3: 'maya' };
const photoThumbSvg = (savedId) => {
  const im = IMAGES.find((x) => x.imageId === Math.floor(savedId / 10));
  const hue = hash(`img${im.imageId}`) % 360;
  const people = im.kids.map((k) => PHOTO_PEOPLE[k]);
  const n = people.length, w = n === 1 ? 300 : n === 2 ? 250 : 200;
  const faces = people.map((who, i) => person(`${who}-${im.i}`, 400 - (n * w) / 2 + i * w + (w - w * 0.9) / 2, 250 + (i % 2) * 30, w * 0.9)).join('');
  return svg(800, 800, `<defs><linearGradient id="s" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="hsl(${hue} 70% 82%)"/><stop offset="1" stop-color="hsl(${hue} 60% 94%)"/></linearGradient></defs>
<rect width="800" height="800" fill="url(#s)"/><rect y="600" width="800" height="200" fill="hsl(${(hue + 100) % 360} 35% 62%)"/><circle cx="680" cy="110" r="50" fill="#ffe27a"/>${faces}`);
};
const sf = (i, kid, score, tier, group, reason = '') => ({
  face_id: 2000 + i, image_id: 400 + i, crop_url: `/api/crops/${KEYNAME[kid]}-${40 + i}`, score, margin: 0.1, tier, kid_id: kid, kid_name: NAMES[kid],
  group_id: group, group_name: GROUPS[group - 1].name, sent_at: iso(100 + i * 90), reason, auto_save_disabled: kid === 2,
});
const SCORE = {
  from: day(14), to: day(0), examined: 61, truncated: false,
  buckets: {
    would_auto_save: { count: 14, items: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11].map((i) => sf(i, 1 + (i % 3 === 1 ? 2 : 0), 0.62 - i * 0.012, 'L', 1 + (i % 3), 'above auto-save threshold')) },
    would_review: { count: 9, items: [0, 1, 2, 3, 4, 5, 6, 7, 8].map((i) => sf(20 + i, 1 + (i % 3), 0.43 - i * 0.008, i % 2 ? 'M' : 'S', 1 + (i % 3), 'between review and auto-save')) },
    would_drop: { count: 38, items: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map((i) => sf(40 + i, 1 + (i % 3), 0.29 - i * 0.01, 'M', 1 + (i % 3), 'below review threshold')) },
  },
  per_kid: [
    { kid_id: 1, kid_name: 'Noa', would_auto_save: 6, would_review: 3, would_drop: 14 },
    { kid_id: 2, kid_name: 'Itai', would_auto_save: 2, would_review: 4, would_drop: 12 },
    { kid_id: 3, kid_name: 'Maya', would_auto_save: 6, would_review: 2, would_drop: 12 },
  ],
};
const CLUSTERS = Array.from({ length: 6 }, (_, i) => ({
  id: 60 + i, size: [34, 22, 17, 12, 9, 6][i], label_status: 'unlabeled', kid_id: null,
  sample_crop_urls: Array.from({ length: 6 }, (_, n) => `/api/crops/c${i}-${n}`), groups: [GROUPS[i % 4].name, GROUPS[(i + 1) % 4].name],
  date_from: day(40 - i * 5), date_to: day(2 + i),
}));
const clusterDetail = (id) => ({
  id, size: 34, label_status: 'unlabeled', kid_id: null, next_cursor: null,
  faces: Array.from({ length: 34 }, (_, n) => ({ face_id: 3000 + n, crop_url: `/api/crops/c${id - 60}-${n % 6}`, quality: 0.5 + (n % 5) * 0.08, size_px: 70 + n * 3, group_name: GROUPS[n % 4].name, sent_at: iso(500 + n * 300) })),
});
const SETTINGS = {
  forward: { enabled: true, default_chat_id: 'chat-family', per_kid: { 2: 'chat-grandma' }, caption_template: '📸 {kids} · {group} · {date}', include_review_confirmed: true },
  digest: { chat_id: 'chat-me', cron: '0 20 * * *', apprise_urls: ['ntfy://'], public_base_url: 'https://kino.example.com' },
  thresholds: { L: { auto: 0.45, review: 0.32 }, M: { auto: 0.5, review: 0.35 }, S: { review: 0.4 }, prior_bonus: 0.03, unmapped_penalty: 0.05, margin_min: 0.08, auto_floor: 0.3 },
  retention: { raw_days: 7, face_days: 30 },
  embedder: 'auraface',
  models: [
    { name: 'SCRFD-10GF detector', filename: 'det_10g.onnx', license: 'InsightFace non-commercial research', sha256: '5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91', present: true },
    { name: 'AuraFace-v1', filename: 'glintr100.onnx', license: 'Apache-2.0', sha256: '4d0a1c23fa1c8c4b2f4d7d9b0d6f8f5f3a2b1c0d9e8f7a6b5c4d3e2f1a0b9c8d', present: true },
  ],
  version: '2026.10.0',
};
const CHATS = [
  { id: 'chat-family', name: 'Family album', is_group: true, watched: false, selectable: true },
  { id: 'chat-grandma', name: 'Grandma Ruth', is_group: false, watched: false, selectable: true },
  { id: 'chat-me', name: 'Me (saved messages)', is_group: false, watched: false, selectable: true },
  { id: 'group-1', name: 'Class 3B parents', is_group: true, watched: true, selectable: false },
  { id: 'group-2', name: 'Football Saturday', is_group: true, watched: true, selectable: false },
];
const job = (o) => ({ id: 7, kind: 'backfill', status: 'running', group_id: 2, params: { days: 30 }, total: 48, processed: 21, skipped: 6, errors: 1, error: null, created_at: iso(3), started_at: iso(3), finished_at: null, ...o });
// two upload results for the Enroll page
const UPLOADS = [
  { upload_id: 'u1', filename: 'park-day.jpg', width: 1200, height: 800, photo_date: day(20).slice(0, 10), error: null, status: 200,
    faces: [
      { temp_face_id: 't1', bbox: LAYOUT[0], quality: 0.88, tier: 'L', selectable: true, reason: null, crop_url: '/api/crops/noa-1' },
      { temp_face_id: 't2', bbox: LAYOUT[1], quality: 0.74, tier: 'L', selectable: true, reason: null, crop_url: '/api/crops/itai-1' },
      { temp_face_id: 't3', bbox: LAYOUT[2], quality: 0.22, tier: 'S', selectable: false, reason: 'too small', crop_url: '/api/crops/friend-1' },
    ] },
  { upload_id: 'u2', filename: 'birthday.jpg', width: 1200, height: 800, photo_date: day(75).slice(0, 10), error: null, status: 200,
    faces: [{ temp_face_id: 't4', bbox: { x: 480, y: 220, w: 220, h: 220 }, quality: 0.93, tier: 'L', selectable: true, reason: null, crop_url: '/api/crops/noa-2' }] },
];
const UPLOAD_FILES = [
  { name: 'park-day.jpg', body: sceneSvg('park') },
  { name: 'birthday.jpg', body: sceneSvg('birthday', [{ x: 480, y: 220, w: 220, h: 220 }], ['noa']) },
];

// ---------------------------------------------------------------- mock API
const unmocked = new Set();
let uploadN = 0;
function mockApi(state) {
  return async (route) => {
    const req = route.request();
    const u = new URL(req.url());
    const p = u.pathname;
    const m = req.method();
    const json = (b, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(b) });
    const img = (body) => route.fulfill({ status: 200, contentType: 'image/svg+xml', body });
    let r;
    if (p === '/api/auth/status') return json({ setup_required: false });
    if (p === '/api/auth/me') return state.anon ? json({ detail: 'Not authenticated' }, 401) : json({ method: 'local', user: 'admin' });
    if (p === '/api/review/count') return json({ total: REVIEW.length, by_kid: { 1: 3, 2: 3, 3: 2 } });
    if (p === '/api/kids' && m === 'GET') return json(KIDS);
    if ((r = p.match(/^\/api\/kids\/(\d+)$/))) return json(KIDS[Number(r[1]) - 1]);
    if ((r = p.match(/^\/api\/kids\/(\d+)\/coverage$/))) return json(coverage(KIDS[Number(r[1]) - 1]));
    if ((r = p.match(/^\/api\/kids\/(\d+)\/references$/))) {
      const status = u.searchParams.get('status');
      return json(status === 'retired' ? [] : refsFor(KIDS[Number(r[1]) - 1]));
    }
    if ((r = p.match(/^\/api\/kids\/(\d+)\/uploads$/))) return json([UPLOADS[uploadN++ % 2]]);
    if (p === '/api/groups') return json(GROUPS);
    if (p === '/api/jobs') return json(state.jobs ? [job({})] : []);
    if (p === '/api/jobs/7') return json(job({}));
    if (p === '/api/stats') return json({ images: { done: 128, skipped: 14, failed: 1 }, queue_depth: 2, review_count: REVIEW.length, forwards: { sent: 96, failed: FAILED.length, pending: 1 }, forward_queue_depth: 1 });
    if (p === '/api/images') return json({ items: ACT, next_cursor: null });
    if ((r = p.match(/^\/api\/images\/(\d+)\/file$/))) return img(sceneSvg(`img${r[1]}`));
    if ((r = p.match(/^\/api\/images\/(\d+)$/))) return json(detail(Number(r[1])));
    if ((r = p.match(/^\/api\/crops\/(.+)$/))) return img(cropSvg(r[1]));
    if (p === '/api/forwards') return json(u.searchParams.get('status') === 'failed' ? FAILED : u.searchParams.get('status') === 'pending' ? PENDING : []);
    if (p === '/api/review') return json({ items: REVIEW, next_cursor: null });
    if (p === '/api/photos/counts') return json(PHOTO_COUNTS);
    if (p === '/api/photos') return json({ items: photosFor(Number(u.searchParams.get('kid_id')) || 0), next_cursor: null });
    if ((r = p.match(/^\/api\/photos\/(\d+)\/thumb$/))) return img(photoThumbSvg(Number(r[1])));
    if (p === '/api/settings') return json(SETTINGS);
    if (p === '/api/openwa/chats') { const q = (u.searchParams.get('q') || '').toLowerCase(); return json(CHATS.filter((c) => (c.name || '').toLowerCase().includes(q))); }
    if (p === '/api/openwa/webhook') return json({ registered: true, active: true, id: 'wh-1', url: 'http://kino-api:8080/api/webhook/openwa', events: ['message.received'] });
    if (p === '/api/test/score') return json(SCORE);
    if (p === '/api/clusters') return json({ items: CLUSTERS, next_cursor: null });
    if ((r = p.match(/^\/api\/clusters\/(\d+)$/))) return json(clusterDetail(Number(r[1])));
    unmocked.add(`${m} ${p}`);
    return json({ detail: 'not mocked' }, 404);
  };
}

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
    uploadN = 0;
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
await surface('settings-forwarding', (p) => open(p, '/settings', 'text=Forwarding'));
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

await browser.close();
stop();
if (unmocked.size) console.warn('UNMOCKED API calls:', [...unmocked].join(', '));
if (consoleErrors.length) console.warn('Console errors:\n' + consoleErrors.join('\n'));
if (unmocked.size) process.exit(1);
