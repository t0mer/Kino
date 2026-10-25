/** Activity API (CLAUDE.md §11.7, §9.9, §12) and the pure helpers the Activity page uses. */

import { api } from "./api";
import type { Channel } from "./telegram";

export const IMAGE_STATUSES = ["queued", "processing", "done", "failed", "skipped"] as const;
export type ImageStatus = (typeof IMAGE_STATUSES)[number];

export interface ActivityImage {
  id: number;
  group_id: number | null;
  group_name: string | null;
  sender: string | null;
  sent_at: string | null;
  received_at: string;
  status: string;
  skip_reason: string | null;
  processing_ms: number | null;
  error: string | null;
  face_count: number;
  matches: Record<string, number>;
  has_file: boolean;
}

export interface ActivityPage {
  items: ActivityImage[];
  next_cursor: number | null;
}

export interface ImageFace {
  id: number;
  bbox: [number, number, number, number];
  det_score: number;
  size_px: number;
  quality: number | null;
  tier: string;
  from_tile: boolean;
  crop_url: string | null;
}

export interface ImageMatch {
  id: number;
  face_id: number;
  kid_id: number;
  kid_name: string;
  score: number;
  margin: number | null;
  method: string;
  decision: string;
  decided_by: string;
}

export interface ImageForward {
  id: number;
  channel?: Channel;
  status: string;
  attempts: number;
  kid_ids: number[];
  last_error: string | null;
  created_at: string;
  sent_at: string | null;
}

export interface ImageDetail {
  id: number;
  group_id: number | null;
  group_name: string | null;
  sender: string | null;
  sent_at: string | null;
  received_at: string;
  width: number | null;
  height: number | null;
  status: string;
  skip_reason: string | null;
  duplicate_of: number | null;
  processing_ms: number | null;
  error: string | null;
  has_file: boolean;
  file_url: string | null;
  faces: ImageFace[];
  matches: ImageMatch[];
  forwards: ImageForward[];
}

export interface Forward {
  id: number;
  channel?: Channel;
  image_id: number;
  dest_chat_id: string;
  kid_ids: number[];
  caption: string | null;
  status: string;
  attempts: number;
  last_error: string | null;
  created_at: string;
  sent_at: string | null;
  next_attempt_at: string | null;
}

export interface Stats {
  images: Record<string, number>;
  queue_depth: number;
  review_count: number;
  forwards: Record<string, number>;
  forward_queue_depth: number;
}

export interface ActivityFilters {
  status: string;
  groupId: string;
}

export const EMPTY_FILTERS: ActivityFilters = { status: "", groupId: "" };

export const activityKeys = {
  all: ["activity"] as const,
  list: (f: ActivityFilters) => ["activity", "list", f] as const,
  detail: (id: number) => ["activity", "detail", id] as const,
  forwards: (status: string) => ["activity", "forwards", status] as const,
  stats: ["activity", "stats"] as const,
};

export const activityApi = {
  list: (f: ActivityFilters, cursor?: number | null) =>
    api.get<ActivityPage>("/api/images", {
      query: { status: f.status || undefined, group_id: f.groupId || undefined, cursor: cursor ?? undefined },
    }),
  detail: (id: number) => api.get<ImageDetail>(`/api/images/${id}`),
  forwards: (status: string) => api.get<Forward[]>("/api/forwards", { query: { status } }),
  retry: (id: number) => api.post<Forward>(`/api/forwards/${id}/retry`),
  stats: () => api.get<Stats>("/api/stats"),
};

export function hasActiveFilters(f: ActivityFilters): boolean {
  return Boolean(f.status || f.groupId);
}

/** Flatten pages, dropping rows repeated across page boundaries. */
export function flattenPages(pages: ActivityPage[]): ActivityImage[] {
  const seen = new Set<number>();
  const out: ActivityImage[] = [];
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

export const channelLabel = (c?: Channel): string => (c === "telegram" ? "Telegram" : "WhatsApp");

export type BadgeTone = "default" | "secondary" | "destructive" | "outline";

export function statusTone(status: string): BadgeTone {
  switch (status) {
    case "done":
    case "sent":
      return "default";
    case "failed":
      return "destructive";
    case "queued":
    case "processing":
    case "pending":
      return "secondary";
    default:
      return "outline";
  }
}

/** "skipped (duplicate)" when a reason is present. */
export function statusLabel(status: string, skipReason?: string | null): string {
  return skipReason ? `${status} (${skipReason})` : status;
}

/** "2 saved, 1 review" from the per-decision match counts; empty when none. */
export function matchSummary(matches: Record<string, number>): string {
  return Object.entries(matches)
    .filter(([, n]) => n > 0)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([d, n]) => `${n} ${d.replace(/_/g, " ")}`)
    .join(", ");
}

export function formatMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`;
}

/** A face box as CSS percentages of the image's natural size. */
export function boxStyle(
  bbox: readonly [number, number, number, number],
  size: { w: number; h: number },
): { left: string; top: string; width: string; height: string } {
  const [x, y, w, h] = bbox;
  return {
    left: `${(x / size.w) * 100}%`,
    top: `${(y / size.h) * 100}%`,
    width: `${(w / size.w) * 100}%`,
    height: `${(h / size.h) * 100}%`,
  };
}
