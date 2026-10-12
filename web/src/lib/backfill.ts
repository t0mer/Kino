/** Backfill + jobs API (CLAUDE.md §8.2, §12) and the pure helpers the backfill UI uses. */

import { api } from "./api";

export const BACKFILL_KIND = "backfill";
export const MIN_DAYS = 1;
export const MAX_DAYS = 90;
export const DEFAULT_DAYS = 30;
export const JOB_POLL_MS = 2000;

export type JobStatus = "queued" | "running" | "done" | "failed";

export interface Job {
  id: number;
  kind: string;
  status: JobStatus;
  group_id: number | null;
  params: Record<string, unknown>;
  total: number;
  processed: number;
  skipped: number;
  errors: number;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export const jobKeys = {
  backfillList: ["jobs", "list", BACKFILL_KIND] as const,
  one: (id: number) => ["jobs", "one", id] as const,
};

export const jobsApi = {
  get: (id: number) => api.get<Job>(`/api/jobs/${id}`),
  list: (kind: string = BACKFILL_KIND, limit = 50) => api.get<Job[]>("/api/jobs", { query: { kind, limit } }),
  startBackfill: (groupId: number, days: number) =>
    api.post<{ job_id: number }>(`/api/groups/${groupId}/backfill?days=${days}`),
};

export function isActive(status: JobStatus): boolean {
  return status === "queued" || status === "running";
}

/** react-query `refetchInterval` for a job: poll while queued/running, stop at done/failed. */
export function jobRefetchInterval(job: Job | undefined): number | false {
  return job && !isActive(job.status) ? false : JOB_POLL_MS;
}

/** Parse the days field; null unless it is a whole number within 1..90. */
export function parseDays(raw: string): number | null {
  const s = raw.trim();
  if (!/^\d+$/.test(s)) return null;
  const n = Number(s);
  return n >= MIN_DAYS && n <= MAX_DAYS ? n : null;
}

/** Newest active job per group id (the list is newest-first, but do not rely on it). */
export function activeJobsByGroup(jobs: Job[] | undefined): Record<number, number> {
  const out: Record<number, number> = {};
  const best: Record<number, number> = {};
  for (const j of jobs ?? []) {
    if (j.kind !== BACKFILL_KIND || j.group_id === null || !isActive(j.status)) continue;
    if (best[j.group_id] === undefined || j.id > best[j.group_id]!) {
      best[j.group_id] = j.id;
      out[j.group_id] = j.id;
    }
  }
  return out;
}

/** Fraction 0..1 when the total is known, else null (indeterminate). */
export function jobFraction(job: Pick<Job, "total" | "processed" | "skipped" | "errors">): number | null {
  if (job.total <= 0) return null;
  return Math.min(1, (job.processed + job.skipped + job.errors) / job.total);
}

const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? "" : "s"}`;

/** One-line status text for a job. */
export function jobSummary(job: Job): string {
  const parts: string[] = [];
  if (job.status === "queued") return "Queued, waiting for a worker";
  if (job.status === "running" && job.total <= 0) return "Fetching message history";
  const base = job.total > 0 ? `${job.processed} of ${plural(job.total, "photo")} imported` : `${plural(job.processed, "photo")} imported`;
  parts.push(base);
  if (job.skipped > 0) parts.push(`${job.skipped} skipped`);
  if (job.errors > 0) parts.push(plural(job.errors, "error"));
  return parts.join(" · ");
}
