/** OpenWA setup wizard API (spec: 2026-10-12 OpenWA setup wizard) and its pure helpers. */

import { api } from "./api";
import type { WebhookRegistered } from "./settings";

export type OpenWAField = "url" | "api_key" | "session_id" | "webhook_secret";

export const ENV_NAMES: Record<OpenWAField, string> = {
  url: "KINO_OPENWA_URL",
  api_key: "KINO_OPENWA_API_KEY",
  session_id: "KINO_OPENWA_SESSION_ID",
  webhook_secret: "KINO_WEBHOOK_SECRET",
};

export interface OpenWASetupStatus {
  configured: boolean;
  /** Without credentials or query string. */
  url: string | null;
  session_id: string | null;
  session_name: string | null;
  has_api_key: boolean;
  has_webhook_secret: boolean;
  /** A new webhook secret is saved but not registered with OpenWA yet (finish the wizard). */
  webhook_secret_pending: boolean;
  webhook_url: string;
  env_overrides: OpenWAField[];
}

export interface OpenWASession {
  id: string;
  name: string | null;
  status: string;
  /** Masked by the API, e.g. 9725****123. */
  phone_masked: string | null;
}

export interface ConnectBody {
  url?: string;
  api_key?: string;
  webhook_secret?: string;
}

export interface ConnectResult {
  sessions: OpenWASession[];
  generated_webhook_secret: boolean;
}

export interface SessionResult {
  session_id: string;
  registered: boolean;
  action: string | null;
  webhook_id: string | null;
  webhook_url: string;
  error: string | null;
}

export interface ConnectForm {
  url: string;
  apiKey: string;
  webhookSecret: string;
}

export const openwaSetupKeys = { status: ["openwa", "setup", "status"] as const };

export const openwaSetupApi = {
  status: () => api.get<OpenWASetupStatus>("/api/openwa/setup/status"),
  connect: (b: ConnectBody) => api.post<ConnectResult>("/api/openwa/setup/connect", b),
  pickSession: (session_id: string, webhook_url?: string) =>
    api.post<SessionResult>("/api/openwa/setup/session", webhook_url ? { session_id, webhook_url } : { session_id }),
  registerWebhook: (url: string) => api.post<WebhookRegistered>("/api/openwa/webhook/register", { url }),
};

export const MIN_WEBHOOK_SECRET = 16;
const PRINTABLE = /^[\x21-\x7e]+$/;
const HTTP_URL = /^https?:\/\/[^\s/?#]+/i;

type StatusLike = { env_overrides: readonly OpenWAField[]; has_api_key: boolean; url: string | null };
export type ConnectErrors = Partial<Record<"url" | "apiKey" | "webhookSecret", string>>;

/** Field errors for the Connect step (empty when it may be sent). Blank keeps a stored value. */
export function connectErrors(f: ConnectForm, s: StatusLike): ConnectErrors {
  const out: ConnectErrors = {};
  const locked = new Set<OpenWAField>(s.env_overrides);
  const url = f.url.trim();
  if (!locked.has("url")) {
    if (!url && !s.url) out.url = "Enter the OpenWA URL.";
    else if (url && !HTTP_URL.test(url)) out.url = "Use an http:// or https:// address, e.g. http://openwa:2785.";
  }
  if (!locked.has("api_key") && !f.apiKey.trim() && !s.has_api_key) out.apiKey = "Enter the OpenWA API key.";
  const secret = f.webhookSecret.trim();
  if (!locked.has("webhook_secret") && secret) {
    if ([...secret].length < MIN_WEBHOOK_SECRET)
      out.webhookSecret = `Use at least ${MIN_WEBHOOK_SECRET} characters, or leave it empty.`;
    else if (!PRINTABLE.test(secret)) out.webhookSecret = "Use letters, digits and symbols only (no spaces).";
  }
  return out;
}

/** Request body: blank and env-locked fields are left out (blank keeps the stored value). */
export function connectBody(f: ConnectForm, locked: readonly OpenWAField[]): ConnectBody {
  const b: ConnectBody = {};
  const url = f.url.trim();
  const key = f.apiKey.trim();
  const secret = f.webhookSecret.trim();
  if (url && !locked.includes("url")) b.url = url;
  if (key && !locked.includes("api_key")) b.api_key = key;
  if (secret && !locked.includes("webhook_secret")) b.webhook_secret = secret;
  return b;
}

/** 64 hex characters from the browser CSPRNG (same strength as the server's token_hex(32)). */
export function generateWebhookSecret(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export function sessionReady(s: OpenWASession): boolean {
  return s.status.toLowerCase() === "ready";
}
