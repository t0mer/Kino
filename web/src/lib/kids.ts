/** Kids API (CLAUDE.md §11.1, §12) and the pure helpers the Kids page uses. */

import { api, isApiError } from "./api";

export interface Kid {
  id: number;
  display_name: string;
  slug: string;
  birth_year: number | null;
  active: boolean;
  auto_save_enabled: boolean;
  threshold_offset: number;
  group_ids: number[];
  reference_count: number;
  today: number;
  last_7_days: number;
  created_at: string;
}

export interface KidCreate {
  display_name: string;
  slug?: string | null;
  birth_year?: number | null;
  active?: boolean;
  threshold_offset?: number;
  group_ids?: number[];
}

export type KidUpdate = Partial<Omit<KidCreate, "display_name">> & {
  display_name?: string;
  auto_save_enabled?: boolean;
};

export interface KidDeleted {
  id: number;
  removed_files: number;
  cancelled_forwards: number;
}

export interface Coverage {
  kid_id: number;
  count: number;
  recommended: number;
  yaw_known: number;
  left: number;
  frontal: number;
  right: number;
  older_than_year: number;
  hints: string[];
}

export interface Reference {
  id: number;
  kid_id: number;
  source: string;
  status: string;
  quality: number | null;
  photo_date: string | null;
  crop_url: string | null;
}

export interface Group {
  id: number;
  chat_id: string;
  name: string | null;
  watched: boolean;
  kid_ids: number[];
  is_forward_destination: boolean;
}

/**
 * Bounds of a kid's threshold offset. They mirror `OFFSET_MIN` / `OFFSET_MAX` in
 * kino/pipeline/decide.py (not exposed by the API); the backend validates them anyway.
 */
export const OFFSET_MIN = -0.1;
export const OFFSET_MAX = 0.3;
export const OFFSET_STEP = 0.01;
export const BIRTH_YEAR_MIN = 1900;
export const BIRTH_YEAR_MAX = 2100;
export const NAME_MAX = 100;
export const SLUG_MAX = 40;

export const kidKeys = {
  all: ["kids"] as const,
  list: ["kids", "list"] as const,
  detail: (id: number) => ["kids", id, "detail"] as const,
  coverage: (id: number) => ["kids", id, "coverage"] as const,
  avatar: (id: number) => ["kids", id, "references", "active"] as const,
};
export const groupKeys = { list: ["groups", "list"] as const };

export const kidsApi = {
  list: () => api.get<Kid[]>("/api/kids"),
  get: (id: number) => api.get<Kid>(`/api/kids/${id}`),
  create: (body: KidCreate) => api.post<Kid>("/api/kids", body),
  update: (id: number, body: KidUpdate) => api.patch<Kid>(`/api/kids/${id}`, body),
  remove: (id: number) => api.delete<KidDeleted>(`/api/kids/${id}`),
  coverage: (id: number) => api.get<Coverage>(`/api/kids/${id}/coverage`),
  activeReferences: (id: number) =>
    api.get<Reference[]>(`/api/kids/${id}/references`, { query: { status: "active", limit: 200 } }),
  groups: () => api.get<Group[]>("/api/groups"),
};

/** Up to two initials ("Noa Levi" -> "NL", "נועה" -> "נ"), for the avatar fallback. */
export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  const first = (w: string | undefined) => (w ? Array.from(w)[0]!.toLocaleUpperCase() : "");
  if (words.length === 0) return "?";
  if (words.length === 1) return first(words[0]);
  return first(words[0]) + first(words[words.length - 1]);
}

/** The highest-quality active reference that has a crop (the kid's avatar), if any. */
export function bestReference(refs: Reference[]): Reference | null {
  let best: Reference | null = null;
  for (const r of refs) {
    if (r.status !== "active" || !r.crop_url) continue;
    if (!best || (r.quality ?? -1) > (best.quality ?? -1)) best = r;
  }
  return best;
}

/** Coverage meter fill, 0..100 (count relative to the recommended minimum). */
export function coveragePercent(count: number, recommended: number): number {
  if (recommended <= 0) return 100;
  return Math.max(0, Math.min(100, Math.round((count / recommended) * 100)));
}

/** "+0.05", "−0.03", "0.00" for the threshold offset. */
export function formatOffset(v: number): string {
  const s = Math.abs(v).toFixed(2);
  if (Number(s) === 0) return "0.00";
  return v > 0 ? `+${s}` : `−${s}`;
}

export function clampOffset(v: number): number {
  if (!Number.isFinite(v)) return 0;
  const c = Math.min(OFFSET_MAX, Math.max(OFFSET_MIN, v));
  return Math.round(c / OFFSET_STEP) * OFFSET_STEP;
}

/** The kid form's state (strings for text inputs). */
export interface KidForm {
  display_name: string;
  slug: string;
  birth_year: string;
  active: boolean;
  threshold_offset: number;
  group_ids: number[];
}

export function emptyForm(): KidForm {
  return { display_name: "", slug: "", birth_year: "", active: true, threshold_offset: 0, group_ids: [] };
}

export function formFromKid(k: Kid): KidForm {
  return {
    display_name: k.display_name,
    slug: k.slug,
    birth_year: k.birth_year === null ? "" : String(k.birth_year),
    active: k.active,
    threshold_offset: k.threshold_offset,
    group_ids: [...k.group_ids],
  };
}

export type FormErrors = Partial<Record<"display_name" | "slug" | "birth_year" | "form", string>>;

/** Client-side checks that mirror the request schema's bounds (the server re-checks). */
export function validateForm(f: KidForm): FormErrors {
  const errors: FormErrors = {};
  const name = f.display_name.trim();
  if (!name) errors.display_name = "Enter a name.";
  else if (name.length > NAME_MAX) errors.display_name = `At most ${NAME_MAX} characters.`;
  if (f.slug.trim().length > SLUG_MAX) errors.slug = `At most ${SLUG_MAX} characters.`;
  const y = f.birth_year.trim();
  if (y) {
    const n = Number(y);
    if (!Number.isInteger(n) || n < BIRTH_YEAR_MIN || n > BIRTH_YEAR_MAX) {
      errors.birth_year = `Enter a year between ${BIRTH_YEAR_MIN} and ${BIRTH_YEAR_MAX}.`;
    }
  }
  return errors;
}

function sameIds(a: number[], b: number[]): boolean {
  const sa = new Set(a);
  const sb = new Set(b);
  return sa.size === sb.size && [...sa].every((v) => sb.has(v));
}

/** Body for POST /api/kids. An empty slug lets the backend transliterate one from the name. */
export function createBody(f: KidForm): KidCreate {
  const y = f.birth_year.trim();
  return {
    display_name: f.display_name.trim(),
    slug: f.slug.trim() || null,
    birth_year: y ? Number(y) : null,
    active: f.active,
    threshold_offset: clampOffset(f.threshold_offset),
    group_ids: [...f.group_ids].sort((a, b) => a - b),
  };
}

/** Body for PATCH /api/kids/{id}: only changed fields; a cleared birth year is sent as null. */
export function updateBody(f: KidForm, k: Kid): KidUpdate {
  const body: KidUpdate = {};
  const name = f.display_name.trim();
  if (name !== k.display_name) body.display_name = name;
  const slug = f.slug.trim();
  if (slug && slug !== k.slug) body.slug = slug;
  const y = f.birth_year.trim();
  const year = y ? Number(y) : null;
  if (year !== k.birth_year) body.birth_year = year;
  if (f.active !== k.active) body.active = f.active;
  const off = clampOffset(f.threshold_offset);
  if (Math.abs(off - k.threshold_offset) > 1e-9) body.threshold_offset = off;
  if (!sameIds(f.group_ids, k.group_ids)) body.group_ids = [...f.group_ids].sort((a, b) => a - b);
  return body;
}

/** Map a backend error to the form field it is about (409 name/slug clash, 422 validation). */
export function errorsFromApi(e: unknown): FormErrors {
  const detail = isApiError(e) ? e.detail || e.message : e instanceof Error ? e.message : "";
  const msg = detail || "Could not save the kid.";
  if (isApiError(e) && (e.status === 409 || e.status === 422)) {
    if (/\bslug\b/i.test(msg)) return { slug: msg };
    if (/display_name|a kid named|display name/i.test(msg)) return { display_name: msg };
    if (/birth_year/i.test(msg)) return { birth_year: msg };
  }
  return { form: msg };
}

export function groupLabel(g: Pick<Group, "name" | "chat_id">): string {
  return g.name?.trim() || g.chat_id;
}
