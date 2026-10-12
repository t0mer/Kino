/** Settings API (CLAUDE.md §9.9, §11.7, §12) and the pure helpers the Settings page uses. */

import { api } from "./api";

export interface ForwardSettings {
  enabled: boolean;
  default_chat_id: string | null;
  per_kid: Record<string, string>;
  caption_template: string;
  include_review_confirmed: boolean;
  send_caption: boolean;
}

export interface DigestOut {
  chat_id: string | null;
  cron: string;
  /** Masked by the API (scheme only). */
  apprise_urls: string[];
  public_base_url: string | null;
}

export interface DigestForm {
  chat_id: string | null;
  cron: string;
  apprise_urls: string[];
  public_base_url: string;
}

export interface TierAutoReview {
  auto: number;
  review: number;
}

export interface Thresholds {
  L: TierAutoReview;
  M: TierAutoReview;
  S: { review: number };
  prior_bonus: number;
  unmapped_penalty: number;
  margin_min: number;
}

export interface ThresholdsOut extends Thresholds {
  auto_floor: number;
}

export interface Retention {
  raw_days: number;
  face_days: number;
}

export interface ModelInfo {
  name: string;
  filename: string;
  license: string;
  sha256: string;
  present: boolean;
}

export interface SettingsOut {
  forward: ForwardSettings;
  digest: DigestOut;
  thresholds: ThresholdsOut;
  retention: Retention;
  embedder: string;
  models: ModelInfo[];
  version: string;
}

export interface Chat {
  id: string;
  name: string | null;
  is_group: boolean;
  watched: boolean;
  selectable: boolean;
}

export interface WebhookStatus {
  registered: boolean;
  active: boolean | null;
  id: string | null;
  url: string | null;
  events: string[];
}

export interface WebhookRegistered {
  action: string;
  id: string;
  url: string;
  events: string[];
}

export const settingsKeys = {
  all: ["settings"] as const,
  chats: (q: string) => ["openwa", "chats", q] as const,
  webhook: ["openwa", "webhook"] as const,
};

export const settingsApi = {
  get: () => api.get<SettingsOut>("/api/settings"),
  putForward: (b: ForwardSettings) => api.put<ForwardSettings>("/api/settings/forward", b),
  putDigest: (b: {
    chat_id: string | null;
    cron: string;
    apprise_urls: string[];
    public_base_url: string | null;
  }) => api.put<DigestOut>("/api/settings/digest", b),
  putThresholds: (b: Thresholds) => api.put<ThresholdsOut>("/api/settings/thresholds", b),
  putRetention: (b: Retention) => api.put<Retention>("/api/settings/retention", b),
  chats: (q: string) => api.get<Chat[]>("/api/openwa/chats", { query: { q } }),
  testForward: (chat_id: string) => api.post<{ sent: boolean }>("/api/forward/test", { chat_id }),
  webhook: () => api.get<WebhookStatus>("/api/openwa/webhook"),
  registerWebhook: () => api.post<WebhookRegistered>("/api/openwa/webhook/register", {}),
};

// --- caption preview ----------------------------------------------------------------------

export const CAPTION_PLACEHOLDERS = ["kids", "group", "sender", "date", "time"] as const;
export const CAPTION_MAX = 1024;
const PLACEHOLDER_RE = /\{(\w+)\}/g;

export interface CaptionSample {
  kids: string[];
  group: string;
  sender: string;
  date: string;
  time: string;
}

export function sampleCaption(kidNames: string[]): CaptionSample {
  return {
    kids: kidNames.length ? kidNames.slice(0, 2) : ["Noa", "Itai"],
    group: "Class 3B",
    sender: "Dana",
    date: "2026-10-12",
    time: "16:30",
  };
}

/** Client-side mirror of the backend's render: known placeholders filled, unknown ones literal. */
export function previewCaption(template: string, s: CaptionSample): string {
  const values: Record<string, string> = {
    kids: s.kids.join(", "),
    group: s.group,
    sender: s.sender,
    date: s.date,
    time: s.time,
  };
  return template.replace(PLACEHOLDER_RE, (m, name: string) => values[name] ?? m);
}

/** Placeholders in the template that Kino does not know (they would be sent literally). */
export function unknownPlaceholders(template: string): string[] {
  const known = new Set<string>(CAPTION_PLACEHOLDERS);
  const out = new Set<string>();
  for (const m of template.matchAll(PLACEHOLDER_RE)) if (m[1] !== undefined && !known.has(m[1])) out.add(m[0]);
  return [...out];
}

// --- dirty detection / errors -------------------------------------------------------------

/** Structural equality for plain JSON-like values (key order ignored). */
export function isEqual(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const ka = Object.keys(a as object);
  const kb = Object.keys(b as object);
  if (ka.length !== kb.length) return false;
  return ka.every((k) => k in (b as object) && isEqual((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k]));
}

export function isDirty<T>(draft: T | null, saved: T): boolean {
  return draft !== null && !isEqual(draft, saved);
}

export interface ParsedErrors {
  fields: Record<string, string>;
  /** Messages without a field. */
  general: string[];
}

/**
 * Split an {@link ApiError} detail built by `errorDetail` ("field.sub: message; other: message")
 * into per-field messages. Plain-text details end up in `general`.
 */
export function parseFieldErrors(detail: string): ParsedErrors {
  const fields: Record<string, string> = {};
  const general: string[] = [];
  for (const part of detail.split("; ")) {
    const m = /^([A-Za-z_][\w.]*): (.+)$/.exec(part);
    if (m?.[1] && m[2]) fields[m[1]] = m[2].replace(/^Value error, /, "");
    else if (part.trim()) general.push(part.trim());
  }
  return { fields, general };
}

/** Message for a form field (exact path, or any path below it). */
export function fieldError(errors: ParsedErrors, path: string): string | undefined {
  if (errors.fields[path]) return errors.fields[path];
  const key = Object.keys(errors.fields).find((k) => k.startsWith(`${path}.`));
  return key ? errors.fields[key] : undefined;
}

// --- thresholds ---------------------------------------------------------------------------

/** Pipeline defaults (kino.pipeline.decide.Thresholds); used only for "Reset to defaults". */
export const DEFAULT_THRESHOLDS: Thresholds = {
  L: { auto: 0.45, review: 0.32 },
  M: { auto: 0.5, review: 0.35 },
  S: { review: 0.4 },
  prior_bonus: 0.03,
  unmapped_penalty: 0.05,
  margin_min: 0.08,
};

export function toThresholds(t: ThresholdsOut): Thresholds {
  const { auto_floor: _floor, ...rest } = t;
  return rest;
}

/** Quick client-side check; the server stays authoritative. Keys are API field paths. */
export function validateThresholds(t: Thresholds, floor: number): Record<string, string> {
  const errs: Record<string, string> = {};
  const nums: [string, number][] = [
    ["L.auto", t.L.auto],
    ["L.review", t.L.review],
    ["M.auto", t.M.auto],
    ["M.review", t.M.review],
    ["S.review", t.S.review],
    ["prior_bonus", t.prior_bonus],
    ["unmapped_penalty", t.unmapped_penalty],
    ["margin_min", t.margin_min],
  ];
  for (const [k, v] of nums) if (!Number.isFinite(v) || v < -1 || v > 1) errs[k] = "Enter a number between -1 and 1";
  for (const tier of ["L", "M"] as const) {
    const { auto, review } = t[tier];
    if (errs[`${tier}.auto`] || errs[`${tier}.review`]) continue;
    if (auto < review) errs[`${tier}.auto`] = "Auto-save must be at least the review value";
    else if (auto < floor) errs[`${tier}.auto`] = `Auto-save must be at least ${floor.toFixed(2)} (the auto-save floor)`;
  }
  return errs;
}

export function validateRetention(r: Retention): Record<string, string> {
  const errs: Record<string, string> = {};
  for (const k of ["raw_days", "face_days"] as const) {
    const v = r[k];
    if (!Number.isInteger(v) || v < 1 || v > 3650) errs[k] = "Enter a whole number of days from 1 to 3650";
  }
  return errs;
}

export function chatLabel(id: string, names: Map<string, string | null>): string {
  return names.get(id)?.trim() || id;
}

export function digestToForm(d: DigestOut): DigestForm {
  return { chat_id: d.chat_id, cron: d.cron, apprise_urls: d.apprise_urls, public_base_url: d.public_base_url ?? "" };
}

export function digestPayload(f: DigestForm) {
  return {
    chat_id: f.chat_id,
    cron: f.cron.trim(),
    apprise_urls: f.apprise_urls.map((u) => u.trim()).filter(Boolean),
    public_base_url: f.public_base_url.trim() || null,
  };
}
