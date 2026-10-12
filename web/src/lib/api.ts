/**
 * Typed fetch wrapper for the Kino REST API.
 *
 * - JSON in/out, cookies sent same-origin.
 * - Every state-changing request carries `X-CSRF-Token` = the `kino_csrf` cookie
 *   (double-submit). If the cookie is missing it is obtained first via GET /api/auth/status.
 * - Non-2xx responses throw {@link ApiError} with the HTTP status and FastAPI `detail`.
 * - A 401 calls the handler registered with {@link setUnauthorizedHandler} (redirect to /login),
 *   unless the request opts out with `skipAuthRedirect`.
 */

export const CSRF_COOKIE = "kino_csrf";
export const CSRF_HEADER = "X-CSRF-Token";

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  /** Seconds from a `Retry-After` header (429 lockout), if any. */
  readonly retryAfter: number | null;

  constructor(status: number, detail: string, retryAfter: number | null = null) {
    super(detail || `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.retryAfter = retryAfter;
  }
}

export function isApiError(e: unknown, status?: number): e is ApiError {
  return e instanceof ApiError && (status === undefined || e.status === status);
}

let unauthorizedHandler: (() => void) | null = null;

/** Register what happens when any API call returns 401 (normally: go to /login). */
export function setUnauthorizedHandler(fn: (() => void) | null): void {
  unauthorizedHandler = fn;
}

export function readCookie(name: string): string | null {
  for (const part of document.cookie.split(";")) {
    const eq = part.indexOf("=");
    if (eq < 0) continue;
    if (part.slice(0, eq).trim() === name) {
      const raw = part.slice(eq + 1).trim();
      try {
        return decodeURIComponent(raw);
      } catch {
        return raw;
      }
    }
  }
  return null;
}

let csrfPending: Promise<void> | null = null;

/** Make sure the CSRF cookie exists (the server sets it on GET /api/auth/status). */
export async function ensureCsrf(): Promise<string | null> {
  const existing = readCookie(CSRF_COOKIE);
  if (existing) return existing;
  csrfPending ??= fetch("/api/auth/status", { credentials: "same-origin" })
    .then(() => undefined)
    .finally(() => {
      csrfPending = null;
    });
  await csrfPending;
  return readCookie(CSRF_COOKIE);
}

type ValidationItem = { loc?: unknown[]; msg?: string };

/** FastAPI errors: `{detail: "text"}` or `{detail: [{loc, msg}, ...]}` (422 validation). */
export function errorDetail(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      const msgs = (detail as ValidationItem[])
        .map((d) => {
          const field = Array.isArray(d.loc) ? d.loc.filter((x) => x !== "body").join(".") : "";
          return d.msg ? (field ? `${field}: ${d.msg}` : d.msg) : "";
        })
        .filter(Boolean);
      if (msgs.length) return msgs.join("; ");
    }
  }
  return fallback;
}

export interface RequestOptions {
  method?: string;
  /** JSON body (serialized) or FormData (sent as multipart). */
  body?: unknown;
  query?: Record<string, string | number | boolean | null | undefined>;
  signal?: AbortSignal;
  /** Do not trigger the global 401 handler (e.g. the login form itself). */
  skipAuthRedirect?: boolean;
  /** Return the response body as a Blob instead of parsing it. */
  asBlob?: boolean;
}

export function buildUrl(path: string, query?: RequestOptions["query"]): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null && v !== "") params.append(k, String(v));
  }
  const qs = params.toString();
  return qs ? `${path}${path.includes("?") ? "&" : "?"}${qs}` : path;
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const method = (opts.method ?? "GET").toUpperCase();
  const headers: Record<string, string> = { Accept: opts.asBlob ? "*/*" : "application/json" };
  let body: BodyInit | undefined;

  if (opts.body instanceof FormData) {
    body = opts.body; // browser sets the multipart boundary
  } else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  if (!SAFE_METHODS.has(method)) {
    const token = await ensureCsrf();
    if (token) headers[CSRF_HEADER] = token;
  }

  let res: Response;
  try {
    res = await fetch(buildUrl(path, opts.query), {
      method,
      headers,
      body,
      credentials: "same-origin",
      signal: opts.signal,
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(0, "Cannot reach the Kino server");
  }

  if (res.status === 401 && !opts.skipAuthRedirect) unauthorizedHandler?.();

  if (!res.ok) {
    let parsed: unknown = null;
    try {
      parsed = await res.json();
    } catch {
      /* not JSON */
    }
    const retry = Number(res.headers.get("Retry-After"));
    throw new ApiError(
      res.status,
      errorDetail(parsed, res.statusText || `HTTP ${res.status}`),
      Number.isFinite(retry) && retry > 0 ? retry : null,
    );
  }
  if (opts.asBlob) return (await res.blob()) as T;
  if (res.status === 204 || res.headers.get("Content-Length") === "0") {
    return undefined as T;
  }
  const type = res.headers.get("Content-Type") ?? "";
  if (type.includes("application/json")) return (await res.json()) as T;
  return (await res.text()) as unknown as T;
}

type Body = Omit<RequestOptions, "method" | "body">;

export const api = {
  get: <T>(path: string, opts?: Body) => request<T>(path, { ...opts, method: "GET" }),
  post: <T>(path: string, body?: unknown, opts?: Body) =>
    request<T>(path, { ...opts, method: "POST", body }),
  put: <T>(path: string, body?: unknown, opts?: Body) =>
    request<T>(path, { ...opts, method: "PUT", body }),
  patch: <T>(path: string, body?: unknown, opts?: Body) =>
    request<T>(path, { ...opts, method: "PATCH", body }),
  /** GET a binary body (images) with the same CSRF/401 handling as every other call. */
  blob: (path: string, opts?: Body) => request<Blob>(path, { ...opts, method: "GET", asBlob: true }),
  delete: <T>(path: string, opts?: Body) => request<T>(path, { ...opts, method: "DELETE" }),
  /** Multipart upload: `files` go under `field` (repeated), plus optional extra form fields. */
  upload: <T>(
    path: string,
    files: Iterable<File | Blob>,
    opts: Body & { field?: string; fields?: Record<string, string> } = {},
  ) => {
    const { field = "files", fields, ...rest } = opts;
    const form = new FormData();
    for (const f of files) form.append(field, f);
    for (const [k, v] of Object.entries(fields ?? {})) form.append(k, v);
    return request<T>(path, { ...rest, method: "POST", body: form });
  },
};
