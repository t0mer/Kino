/** Photos API (CLAUDE.md §11.7, §12) and the pure helpers the Photos page uses. */

import { api } from "./api";

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
  remove: (id: number) => api.delete<PhotoRemoved>(`/api/photos/${id}`),
};

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
