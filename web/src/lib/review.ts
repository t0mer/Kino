/** Review queue API (CLAUDE.md §11.4, §12) and the pure helpers the Review page uses. */

import { api, isApiError } from "./api";

export interface NearRef {
  reference_id: number;
  similarity: number;
  crop_url: string | null;
}

export interface ReviewItem {
  match_id: number;
  face_id: number;
  image_id: number;
  kid_id: number;
  kid_name: string;
  score: number;
  margin: number | null;
  tier: string;
  method: string;
  group_id: number | null;
  group_name: string | null;
  sent_at: string | null;
  bbox: { x: number; y: number; w: number; h: number };
  crop_url: string | null;
  image_url: string | null;
  nearest_refs: NearRef[];
}

export interface ReviewPage {
  items: ReviewItem[];
  next_cursor: number | null;
}

export type ReviewAction = "confirm" | "reject" | "someone_else" | "reassign";

export interface ReviewResult {
  match_id: number;
  decision: string;
  kid_id: number;
  changed: boolean;
}

export interface BulkItem {
  match_id: number;
  status: number;
  detail: string | null;
  result: ReviewResult | null;
}

export const reviewKeys = {
  all: ["review"] as const,
  count: ["review", "count"] as const,
  list: (kidId: number | null, method: string | null) => ["review", "list", kidId, method] as const,
};

export const PAGE_SIZE = 30;
/** Fetch the next page when this few undecided items remain in the loaded queue. */
export const PREFETCH_AT = 6;

export const reviewApi = {
  page: (kidId: number | null, method: string | null, cursor: number | null) =>
    api.get<ReviewPage>("/api/review", {
      query: { kid_id: kidId, method, cursor, limit: PAGE_SIZE },
    }),
  decide: (matchId: number, action: ReviewAction, kidId?: number) =>
    api.post<ReviewResult>(`/api/review/${matchId}`, kidId === undefined ? { action } : { action, kid_id: kidId }),
  bulk: (matchIds: number[], action: "confirm" | "reject") =>
    api.post<{ results: BulkItem[] }>("/api/review/bulk", { match_ids: matchIds, action }),
};

/** Maximum number of kids reachable with the 1-9 keys. */
export const MAX_NUMBERED_KIDS = 9;

export type Shortcut =
  | { type: "decide"; action: "confirm" | "reject" | "someone_else" }
  | { type: "reassign"; kidId: number }
  | { type: "skip" };

/**
 * Map a pressed key to a review action. `kidIds` is the ordered kid list shown with numbers
 * (key "1" is the first). Reassigning to the currently suggested kid is not a shortcut.
 */
export function shortcutFor(key: string, kidIds: readonly number[], currentKidId: number): Shortcut | null {
  switch (key.toLowerCase()) {
    case "y":
      return { type: "decide", action: "confirm" };
    case "n":
      return { type: "decide", action: "reject" };
    case "o":
      return { type: "decide", action: "someone_else" };
    case "s":
      return { type: "skip" };
  }
  if (/^[1-9]$/.test(key)) {
    const kidId = kidIds[Number(key) - 1];
    if (kidId !== undefined && kidId !== currentKidId) return { type: "reassign", kidId };
  }
  return null;
}

/** True when the keyboard event should be left alone (typing, modifiers, or an open dialog). */
export function shouldIgnoreKey(e: {
  key: string;
  ctrlKey?: boolean;
  metaKey?: boolean;
  altKey?: boolean;
  target: EventTarget | null;
}): boolean {
  if (e.ctrlKey || e.metaKey || e.altKey) return true;
  const t = e.target;
  if (t instanceof HTMLElement) {
    if (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName)) return true;
    if (t.getAttribute("role") === "combobox") return true;
  }
  return typeof document !== "undefined" && document.querySelector('[role="dialog"], [role="alertdialog"]') !== null;
}

/**
 * The working order of the queue: undecided items in server order, with skipped ones moved
 * to the end in the order they were skipped. Decided items are dropped.
 */
export function queueOrder(
  items: readonly ReviewItem[],
  decided: ReadonlySet<number>,
  skipped: readonly number[],
): ReviewItem[] {
  const live = items.filter((i) => !decided.has(i.match_id));
  const skipRank = new Map(skipped.map((id, n) => [id, n] as const));
  const fresh = live.filter((i) => !skipRank.has(i.match_id));
  const later = live
    .filter((i) => skipRank.has(i.match_id))
    .sort((a, b) => skipRank.get(a.match_id)! - skipRank.get(b.match_id)!);
  return [...fresh, ...later];
}

/** Skipping moves an item to the back: drop an earlier mark then append. */
export function withSkipped(skipped: readonly number[], id: number): number[] {
  return [...skipped.filter((x) => x !== id), id];
}

/** True when more of the queue should be fetched now. */
export function needsPrefetch(remaining: number, hasNext: boolean): boolean {
  return hasNext && remaining <= PREFETCH_AT;
}

/** Statuses meaning the item is gone for good (already decided, or its raw file is gone). */
export function isGone(e: unknown): boolean {
  return isApiError(e, 404) || isApiError(e, 409);
}

export function goneMessage(e: unknown): string {
  if (isApiError(e, 409)) return "Already decided elsewhere. Moving on.";
  return "This photo is no longer available (the raw file is gone). Moving on.";
}

/** Matches a bulk call settled for good: succeeded or gone (404/409). */
export function bulkSettled(results: readonly BulkItem[]): number[] {
  return results.filter((r) => r.status === 200 || r.status === 404 || r.status === 409).map((r) => r.match_id);
}

export function bulkSummary(results: readonly BulkItem[]): { ok: number; failed: BulkItem[] } {
  const failed = results.filter((r) => r.status !== 200);
  return { ok: results.length - failed.length, failed };
}

/** Toggle `id`; with `shiftFrom` set, select the whole range between it and `id` instead. */
export function selectRange(
  order: readonly number[],
  selected: ReadonlySet<number>,
  id: number,
  shiftFrom: number | null,
): Set<number> {
  const next = new Set(selected);
  const a = shiftFrom === null ? -1 : order.indexOf(shiftFrom);
  const b = order.indexOf(id);
  if (a >= 0 && b >= 0) {
    const [lo, hi] = a < b ? [a, b] : [b, a];
    for (const m of order.slice(lo, hi + 1)) next.add(m);
    return next;
  }
  if (next.has(id)) next.delete(id);
  else next.add(id);
  return next;
}

export function formatScore(score: number): string {
  return score.toFixed(2);
}
