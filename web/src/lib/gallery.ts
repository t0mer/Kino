/** Gallery management API (CLAUDE.md §11.6) and the pure helpers the Gallery page uses. */

import { api } from "./api";

export interface GalleryRef {
  id: number;
  kid_id: number;
  face_id: number | null;
  source: string;
  status: string;
  quality: number | null;
  photo_date: string | null;
  added_at: string;
  weight: number;
  yaw: number | null;
  crop_url: string | null;
  /** Set by the nightly gallery-health task (kino/health.py); absent on older backends. */
  outlier?: boolean;
  outlier_reason?: string | null;
}

export interface RefFilters {
  source: string;
  status: string;
  from: string;
  to: string;
}

export const EMPTY_REF_FILTERS: RefFilters = { source: "", status: "", from: "", to: "" };
export const SOURCES = ["upload", "cluster", "review", "auto"] as const;
export const STATUSES = ["active", "retired"] as const;

export const galleryKeys = {
  refs: (kidId: number, source: string, status: string) =>
    ["kids", kidId, "gallery", source, status] as const,
};

export const galleryApi = {
  list: (kidId: number, source: string, status: string) =>
    api.get<GalleryRef[]>(`/api/kids/${kidId}/references`, {
      query: { status: status || null, source: source || null, limit: 500 },
    }),
  setStatus: (id: number, status: "active" | "retired") => api.patch<GalleryRef>(`/api/references/${id}`, { status }),
  move: (id: number, kid_id: number) => api.patch<GalleryRef>(`/api/references/${id}`, { kid_id }),
  setDate: (id: number, date: string | null) =>
    api.patch<GalleryRef>(`/api/references/${id}`, { photo_date: date ? `${date}T00:00:00` : null }),
  remove: (id: number) => api.delete<void>(`/api/references/${id}`),
  toNegative: (id: number) => api.post<unknown>(`/api/references/${id}/to-negative`),
};

/** "YYYY-MM-DD" part of an API timestamp (stored as naive UTC), or "" when unset. */
export function dateOnly(value: string | null | undefined): string {
  return value ? value.slice(0, 10) : "";
}

export function hasRefFilters(f: RefFilters): boolean {
  return Boolean(f.source || f.status || f.from || f.to);
}

export function invalidRefRange(f: Pick<RefFilters, "from" | "to">): boolean {
  return Boolean(f.from && f.to && f.from > f.to);
}

/** Client-side date-range filter (the API has none). Undated references drop out once a bound is set. */
export function filterByDate<T extends { photo_date: string | null }>(refs: T[], from: string, to: string): T[] {
  if (!from && !to) return refs;
  return refs.filter((r) => {
    const d = dateOnly(r.photo_date);
    if (!d) return false;
    return (!from || d >= from) && (!to || d <= to);
  });
}

const OUTLIER_TEXT: Record<string, string> = {
  far_from_centroid: "Looks different from the rest of this kid's references.",
  closer_to_other_kid: "Looks more like another kid than like this one.",
};

/** Plain-language explanation for a backend outlier reason code (generic text for unknown codes). */
export function outlierReasonText(reason: string | null | undefined): string {
  return (reason && OUTLIER_TEXT[reason]) || "Flagged as different from the rest of the gallery.";
}

export function countOutliers(refs: { outlier?: boolean }[]): number {
  return refs.filter((r) => r.outlier).length;
}

export function onlyOutliers<T extends { outlier?: boolean }>(refs: T[]): T[] {
  return refs.filter((r) => r.outlier);
}

/** Outliers first, then newest photo first; undated last; ties by id descending. Does not mutate. */
export function sortRefs<T extends { id: number; photo_date: string | null; outlier?: boolean }>(refs: T[]): T[] {
  return [...refs].sort((a, b) => {
    if (Boolean(a.outlier) !== Boolean(b.outlier)) return a.outlier ? -1 : 1;
    const da = dateOnly(a.photo_date);
    const db = dateOnly(b.photo_date);
    if (da !== db) {
      if (!da) return 1;
      if (!db) return -1;
      return da < db ? 1 : -1;
    }
    return b.id - a.id;
  });
}

export interface MonthBucket {
  key: string; // YYYY-MM
  count: number;
}

export interface Histogram {
  buckets: MonthBucket[];
  undated: number;
  max: number;
}

function nextMonth(key: string): string {
  const [y, m] = key.split("-").map(Number) as [number, number];
  return m === 12 ? `${y + 1}-01` : `${y}-${String(m + 1).padStart(2, "0")}`;
}

/** Reference counts per month between the oldest and newest photo_date (empty months included). */
export function monthHistogram(refs: Pick<GalleryRef, "photo_date">[]): Histogram {
  const counts = new Map<string, number>();
  let undated = 0;
  for (const r of refs) {
    const d = dateOnly(r.photo_date);
    if (!d) undated++;
    else counts.set(d.slice(0, 7), (counts.get(d.slice(0, 7)) ?? 0) + 1);
  }
  const keys = [...counts.keys()].sort();
  const buckets: MonthBucket[] = [];
  if (keys.length > 0) {
    const last = keys[keys.length - 1]!;
    for (let k = keys[0]!; k <= last; k = nextMonth(k)) buckets.push({ key: k, count: counts.get(k) ?? 0 });
  }
  return { buckets, undated, max: Math.max(0, ...buckets.map((b) => b.count)) };
}

/** "Oct 2026" for a YYYY-MM key. */
export function monthLabel(key: string, locale?: string): string {
  const [y, m] = key.split("-").map(Number) as [number, number];
  return new Intl.DateTimeFormat(locale, { month: "short", year: "numeric", timeZone: "UTC" }).format(
    new Date(Date.UTC(y, m - 1, 1)),
  );
}
