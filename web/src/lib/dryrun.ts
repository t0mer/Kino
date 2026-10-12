/** Test (dry-run) API (CLAUDE.md §11.5, §12) and the pure helpers the Test page uses. */

import { api } from "./api";
import type { Thresholds } from "./settings";

export const BUCKETS = ["would_auto_save", "would_review", "would_drop"] as const;
export type BucketName = (typeof BUCKETS)[number];

export interface ScoreFace {
  face_id: number;
  image_id: number;
  crop_url: string | null;
  score: number;
  margin: number;
  tier: string;
  kid_id: number | null;
  kid_name: string | null;
  group_id: number | null;
  group_name: string | null;
  sent_at: string | null;
  reason: string;
  auto_save_disabled: boolean;
}

export interface ScoreBucket {
  count: number;
  items: ScoreFace[];
}

export interface ScoreKidCounts {
  kid_id: number;
  kid_name: string | null;
  would_auto_save: number;
  would_review: number;
  would_drop: number;
}

export interface ScoreResult {
  from: string;
  to: string;
  examined: number;
  truncated: boolean;
  buckets: Record<BucketName, ScoreBucket>;
  per_kid: ScoreKidCounts[];
}

export interface ScoreRequest {
  kidId: string;
  from: string;
  to: string;
  thresholds: Thresholds;
}

export type FlagAction = "review" | "negative";

export interface FlagResult {
  face_id: number;
  kid_id: number;
  action: string;
  changed: boolean;
}

export interface RescoreResult {
  examined: number;
  created: number;
  updated: number;
  unchanged: number;
  protected: number;
  saved: number;
  retired: number;
}

export const dryrunKeys = {
  score: (r: ScoreRequest) => ["test", "score", r] as const,
  all: ["test", "score"] as const,
};

export function scoreBody(r: ScoreRequest) {
  return {
    kid_ids: r.kidId ? [Number(r.kidId)] : undefined,
    from: r.from ? `${r.from}T00:00:00` : undefined,
    to: r.to ? `${r.to}T23:59:59` : undefined,
    thresholds: r.thresholds,
  };
}

export const dryrunApi = {
  score: (r: ScoreRequest, signal?: AbortSignal) =>
    api.post<ScoreResult>("/api/test/score", scoreBody(r), { signal }),
  flag: (face_id: number, kid_id: number, action: FlagAction) =>
    api.post<FlagResult>("/api/test/flag", { face_id, kid_id, action }),
  rescore: (days: number) => api.post<RescoreResult>("/api/rescore", { days }),
};

export const DEFAULT_RANGE_DAYS = 14;
export const RESCORE_DAYS_MAX = 365;

function ymd(d: Date): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** Default Test range: the last `days` days up to today, as local `YYYY-MM-DD`. */
export function defaultRange(now: Date = new Date(), days = DEFAULT_RANGE_DAYS): { from: string; to: string } {
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate() - days);
  return { from: ymd(start), to: ymd(now) };
}

export function invalidRange(from: string, to: string): boolean {
  return Boolean(from && to && from > to);
}

/** Valid whole number of days for "Re-score last N days" (1..365), else null. */
export function parseDays(v: string): number | null {
  const n = Number(v.trim());
  return v.trim() !== "" && Number.isInteger(n) && n >= 1 && n <= RESCORE_DAYS_MAX ? n : null;
}

/** "showing 60 of 213" when a bucket's crop list is capped, else null. */
export function cappedNote(b: ScoreBucket): string | null {
  return b.items.length < b.count ? `Showing ${b.items.length} of ${b.count}` : null;
}

export interface ThresholdChange {
  path: string;
  label: string;
  from: number;
  to: number;
}

const PATHS: [string, string][] = [
  ["L.auto", "Tier L auto-save"],
  ["L.review", "Tier L review"],
  ["M.auto", "Tier M auto-save"],
  ["M.review", "Tier M review"],
  ["S.review", "Tier S review"],
];

function at(t: Thresholds, path: string): number {
  const [tier, key] = path.split(".") as ["L" | "M" | "S", string];
  return (t[tier] as unknown as Record<string, number>)[key] ?? NaN;
}

/** The tier thresholds that differ between the stored and the draft values (for the confirm). */
export function thresholdChanges(saved: Thresholds, draft: Thresholds): ThresholdChange[] {
  return PATHS.flatMap(([path, label]) => {
    const from = at(saved, path);
    const to = at(draft, path);
    return Math.abs(from - to) > 1e-9 ? [{ path, label, from, to }] : [];
  });
}

/** Return `t` with one tier value replaced (rounded to 2 decimals). */
export function withValue(t: Thresholds, path: string, v: number): Thresholds {
  const [tier, key] = path.split(".") as ["L" | "M" | "S", string];
  return { ...t, [tier]: { ...t[tier], [key]: Math.round(v * 100) / 100 } };
}

export function thresholdAt(t: Thresholds, path: string): number {
  return at(t, path);
}

export function rescoreSummary(r: RescoreResult): string {
  return `${r.examined} faces examined: ${r.created} new, ${r.updated} updated, ${r.saved} saved, ${r.protected} kept as decided.`;
}
