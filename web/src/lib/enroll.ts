/** Enroll-faces API (CLAUDE.md §11.2) and the pure helpers the Enroll page uses. */

import { api } from "./api";

export interface BBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface UploadFace {
  temp_face_id: string;
  bbox: BBox;
  quality: number;
  tier: string;
  selectable: boolean;
  reason: string | null;
  crop_url: string;
}

export interface UploadResult {
  upload_id: string;
  filename: string;
  width: number | null;
  height: number | null;
  photo_date: string | null;
  faces: UploadFace[];
  error: string | null;
  status: number;
}

export interface ReferencesAdded {
  created: unknown[];
  duplicates: number;
}

export interface ImageListItem {
  id: number;
  group_id: number | null;
  group_name: string | null;
  sender: string | null;
  sent_at: string | null;
  received_at: string;
  status: string;
  face_count: number;
  has_file: boolean;
}

export interface ImagePage {
  items: ImageListItem[];
  next_cursor: number | null;
}

export const enrollApi = {
  upload: (kidId: number, file: File | Blob) => api.upload<UploadResult[]>(`/api/kids/${kidId}/uploads`, [file]),
  addReferences: (kidId: number, temp_face_ids: string[], photo_date?: string) =>
    api.post<ReferencesAdded>(`/api/kids/${kidId}/references`, {
      temp_face_ids,
      source: "upload",
      ...(photo_date ? { photo_date } : {}),
    }),
  images: (groupId: number | null, cursor: number | null) =>
    api.get<ImagePage>("/api/images", {
      query: { status: "done", group_id: groupId, cursor, limit: 48 },
    }),
  imageBlob: async (id: number): Promise<Blob> => {
    const res = await fetch(`/api/images/${id}/file`, { credentials: "same-origin" });
    if (!res.ok) throw new Error(`Could not load photo ${id} (HTTP ${res.status})`);
    return res.blob();
  },
};

export const imageKeys = {
  list: (groupId: number | null) => ["images", "enroll", groupId] as const,
};

/** Accepted types for the file picker (backend sniffs content; this only guides the user). */
export const ACCEPT = "image/jpeg,image/png,image/webp,image/heic,image/heif,.jpg,.jpeg,.png,.webp,.heic,.heif";
export const MAX_FILE_BYTES = 20 * 1024 * 1024;
const IMAGE_EXT = /\.(jpe?g|png|webp|heic|heif)$/i;

/** Quick client-side check; returns an error message or null. The server re-validates. */
export function checkFile(f: Pick<File, "name" | "type" | "size">): string | null {
  const looksImage = f.type.startsWith("image/") || (!f.type && IMAGE_EXT.test(f.name)) || IMAGE_EXT.test(f.name);
  if (!looksImage) return "Not an image (use JPG, PNG, WebP or HEIC).";
  if (f.size > MAX_FILE_BYTES) return "Larger than 20 MB.";
  return null;
}

/** CSS percentages for a face box over an image of the given pixel size (resolution independent). */
export function boxStyle(b: BBox, width: number, height: number) {
  const pct = (v: number, total: number) => `${((v / total) * 100).toFixed(3)}%`;
  return { left: pct(b.x, width), top: pct(b.y, height), width: pct(b.w, width), height: pct(b.h, height) };
}

/** Scale a box from image pixels to displayed pixels. */
export function scaleBox(b: BBox, imgW: number, imgH: number, shownW: number, shownH: number): BBox {
  const sx = shownW / imgW;
  const sy = shownH / imgH;
  return { x: b.x * sx, y: b.y * sy, w: b.w * sx, h: b.h * sy };
}

export function selectableFaces(r: Pick<UploadResult, "faces">): UploadFace[] {
  return r.faces.filter((f) => f.selectable);
}

/** The single selectable face of a photo (one-click "Use this face"), else null. */
export function soleSelectable(r: Pick<UploadResult, "faces">): UploadFace | null {
  const s = selectableFaces(r);
  return s.length === 1 ? s[0]! : null;
}

/** Toggle one face; unselectable or unknown ids are ignored. Returns a new set. */
export function toggleFace(selected: ReadonlySet<string>, face: Pick<UploadFace, "temp_face_id" | "selectable">): Set<string> {
  const next = new Set(selected);
  if (!face.selectable) return next;
  if (next.has(face.temp_face_id)) next.delete(face.temp_face_id);
  else next.add(face.temp_face_id);
  return next;
}

/** Drop ids that no longer exist (a photo was removed). */
export function pruneSelection(selected: ReadonlySet<string>, results: Pick<UploadResult, "faces">[]): Set<string> {
  const live = new Set(results.flatMap((r) => selectableFaces(r).map((f) => f.temp_face_id)));
  return new Set([...selected].filter((id) => live.has(id)));
}

export function todayISO(now: Date = new Date()): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}`;
}

/** Default date input value for a photo: EXIF date (YYYY-MM-DD) if the backend sent one, else today. */
export function defaultPhotoDate(r: Pick<UploadResult, "photo_date">, now?: Date): string {
  if (r.photo_date && /^\d{4}-\d{2}-\d{2}/.test(r.photo_date)) return r.photo_date.slice(0, 10);
  return todayISO(now);
}

export interface SaveGroup {
  date: string;
  tempFaceIds: string[];
}

/** One POST per distinct date, since the endpoint takes a single photo_date per request. */
export function groupSelectionByDate(
  results: Pick<UploadResult, "upload_id" | "faces">[],
  selected: ReadonlySet<string>,
  dates: Readonly<Record<string, string>>,
  fallbackDate: string,
): SaveGroup[] {
  const by = new Map<string, string[]>();
  for (const r of results) {
    const ids = r.faces.filter((f) => f.selectable && selected.has(f.temp_face_id)).map((f) => f.temp_face_id);
    if (!ids.length) continue;
    const d = dates[r.upload_id] || fallbackDate;
    by.set(d, [...(by.get(d) ?? []), ...ids]);
  }
  return [...by].map(([date, tempFaceIds]) => ({ date, tempFaceIds }));
}

/** Backend wants a datetime; noon avoids timezone edge cases moving the calendar day. */
export function dateToApi(d: string): string {
  return `${d}T12:00:00`;
}
