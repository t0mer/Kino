/** Photos API (CLAUDE.md §11.7, §12) and the pure helpers the Photos page uses. */

import { api } from "./api";
import { parseApiDate } from "./format";

export interface Photo {
  id: number;
  image_id: number;
  kid_id: number;
  kid_name: string;
  group_id: number | null;
  group_name: string | null;
  sent_at: string | null;
  saved_at: string;
  file_url: string;
  thumb_url: string;
  /** Null when the sender is only a phone number (masked by the backend). */
  sender_name: string | null;
  /** The OTHER kids saved from the same photo. */
  kid_ids: number[];
  kid_names: string[];
}

export interface KidPhotoCount {
  kid_id: number;
  kid_name: string;
  count: number;
}

export interface PhotoCounts {
  total: number;
  kids: KidPhotoCount[];
}

export interface PhotoPage {
  items: Photo[];
  next_cursor: number | null;
}

export interface PhotoFilters {
  kidId: string;
  groupId: string;
  from: string;
  to: string;
}

export const EMPTY_FILTERS: PhotoFilters = { kidId: "", groupId: "", from: "", to: "" };

export interface PhotoRemoved {
  id: number;
  removed_files: number;
  cancelled_forwards: number;
  rejected_matches: number;
  retired_references: number;
}

export const photoKeys = {
  all: ["photos"] as const,
  list: (f: PhotoFilters) => ["photos", "list", f] as const,
  counts: (f: PhotoFilters) => ["photos", "counts", { ...f, kidId: "" }] as const,
};

/** Query params for GET /api/photos. A "to" date covers the whole day. */
export function filterQuery(f: PhotoFilters, cursor?: number | null) {
  return {
    kid_id: f.kidId || undefined,
    group_id: f.groupId || undefined,
    from: f.from ? `${f.from}T00:00:00` : undefined,
    to: f.to ? `${f.to}T23:59:59` : undefined,
    cursor: cursor ?? undefined,
  };
}

export function hasActiveFilters(f: PhotoFilters): boolean {
  return Boolean(f.kidId || f.groupId || f.from || f.to);
}

/** True when both dates are set and "from" is after "to". */
export function invalidRange(f: PhotoFilters): boolean {
  return Boolean(f.from && f.to && f.from > f.to);
}

export const photosApi = {
  list: (f: PhotoFilters, cursor?: number | null) =>
    api.get<PhotoPage>("/api/photos", { query: filterQuery(f, cursor) }),
  counts: (f: PhotoFilters) => {
    const { kid_id: _kid, cursor: _cursor, ...q } = filterQuery(f);
    return api.get<PhotoCounts>("/api/photos/counts", { query: q });
  },
  remove: (id: number) => api.delete<PhotoRemoved>(`/api/photos/${id}`),
};

export interface MonthGroup {
  key: string;
  label: string;
  items: Photo[];
}

/** The date a photo is filed under: when it was sent, else when it was saved. */
function photoDate(p: Photo): Date | null {
  return parseApiDate(p.sent_at) ?? parseApiDate(p.saved_at);
}

/** Group photos (already newest first) under local-month headings, keeping their order. */
export function groupByMonth(photos: Photo[], locale?: string): MonthGroup[] {
  const fmt = new Intl.DateTimeFormat(locale, { month: "long", year: "numeric" });
  const out: MonthGroup[] = [];
  for (const p of photos) {
    const d = photoDate(p);
    const key = d ? `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}` : "unknown";
    const last = out.at(-1);
    if (last && last.key === key) last.items.push(p);
    else out.push({ key, label: d ? fmt.format(d) : "Unknown date", items: [p] });
  }
  return out;
}

export interface KidTab {
  /** null = the "All" tab. */
  kidId: number | null;
  name: string;
  count: number;
}

/** "All" plus one tab per kid; kids without photos show 0. */
export function mergeTabs(kids: { id: number; display_name: string }[], counts: PhotoCounts | undefined): KidTab[] {
  const byKid = new Map((counts?.kids ?? []).map((k) => [k.kid_id, k.count]));
  return [
    { kidId: null, name: "All", count: counts?.total ?? 0 },
    ...kids.map((k) => ({ kidId: k.id, name: k.display_name, count: byKid.get(k.id) ?? 0 })),
  ];
}

export const UNKNOWN_SENDER = "Unknown sender";

/** Every saved row (one per kid) for the same image, so the lightbox can name all kids. */
export function siblingsOf(photo: Photo, all: Photo[]): Photo[] {
  return all.filter((p) => p.image_id === photo.image_id);
}

/** Flatten pages, dropping rows repeated across page boundaries. */
export function flattenPages(pages: PhotoPage[]): Photo[] {
  const seen = new Set<number>();
  const out: Photo[] = [];
  for (const p of pages) {
    for (const item of p.items) {
      if (!seen.has(item.id)) {
        seen.add(item.id);
        out.push(item);
      }
    }
  }
  return out;
}
