/** "Who is this?" cluster API (CLAUDE.md §11.3) and its pure helpers. */

import type { QueryKey } from "@tanstack/react-query";
import { activityKeys } from "./activity";
import { api } from "./api";
import { kidKeys } from "./kids";
import { photoKeys } from "./photos";
import { reviewKeys } from "./review";
import { formatDate } from "./format";

export interface ClusterCard {
  id: number;
  size: number;
  label_status: string;
  kid_id: number | null;
  sample_crop_urls: string[];
  groups: string[];
  date_from: string | null;
  date_to: string | null;
}
export interface ClusterPage {
  items: ClusterCard[];
  next_cursor: string | null;
}
export interface ClusterFace {
  face_id: number;
  crop_url: string | null;
  quality: number | null;
  size_px: number;
  group_name: string | null;
  sent_at: string | null;
}
export interface ClusterDetail {
  id: number;
  size: number;
  label_status: string;
  kid_id: number | null;
  faces: ClusterFace[];
  next_cursor: number | null;
}
export interface ClusterLabelResult {
  cluster_id: number;
  kid_id: number;
  selected: number;
  references_created: number;
  duplicates: number;
  rescore: { examined: number; created: number; updated: number; saved: number } | null;
}
export interface ClusterIgnoreResult {
  faces_deleted: number;
  files_removed: number;
}

export const clusterKeys = {
  all: ["clusters"] as const,
  list: ["clusters", "list"] as const,
  detail: (id: number) => ["clusters", "detail", id] as const,
};

/** Labelling adds references and re-scores, which creates review items and may save photos. */
export const labelInvalidations: readonly QueryKey[] = [
  kidKeys.all,
  reviewKeys.all,
  reviewKeys.count,
  photoKeys.all,
  activityKeys.all,
  ["stats"],
];

export const clusterApi = {
  list: (cursor?: string | null) =>
    api.get<ClusterPage>("/api/clusters", { query: { status: "unlabeled", cursor } }),
  get: (id: number, cursor?: number | null) =>
    api.get<ClusterDetail>(`/api/clusters/${id}`, { query: { limit: 200, cursor } }),
  label: (id: number, kidId: number, faceIds: number[] | null) =>
    api.post<ClusterLabelResult>(`/api/clusters/${id}/label`, { kid_id: kidId, face_ids: faceIds }),
  ignore: (id: number) => api.post<ClusterIgnoreResult>(`/api/clusters/${id}/ignore`),
  split: (id: number) => api.post<{ cluster_ids: number[] }>(`/api/clusters/${id}/split`),
  run: () => api.post<{ status: string; job_id: string | null }>("/api/clusters/run"),
};

export const SAMPLE_COUNT = 6;

/** "Oct 1, 2026 – Oct 9, 2026", a single date when both ends fall on one day, "" when unknown. */
export function dateRange(from: string | null, to: string | null): string {
  if (!from && !to) return "";
  const a = formatDate(from ?? to);
  const b = formatDate(to ?? from);
  return a === b ? a : `${a} – ${b}`;
}

/** Up to three group names plus "+N more". */
export function groupsSummary(groups: string[], max = 3): string {
  if (groups.length <= max) return groups.join(", ");
  return `${groups.slice(0, max).join(", ")} +${groups.length - max} more`;
}

export function faceCountLabel(n: number): string {
  return `${n} ${n === 1 ? "face" : "faces"}`;
}

/** Face ids to send to /label: null (= the whole cluster) only when nothing is deselected and every face is loaded. */
export function labelFaceIds(loaded: number[], deselected: ReadonlySet<number>, moreUnloaded: boolean): number[] | null {
  const chosen = loaded.filter((id) => !deselected.has(id));
  return deselected.size === 0 && moreUnloaded ? null : chosen;
}

/** Face ids of every page after `cursor` (the loaded pages are not re-fetched). */
export async function remainingFaceIds(
  clusterId: number,
  cursor: number | null,
  fetchPage: (id: number, cursor: number | null) => Promise<ClusterDetail> = clusterApi.get,
): Promise<number[]> {
  const ids: number[] = [];
  let next = cursor;
  while (next !== null) {
    const page = await fetchPage(clusterId, next);
    ids.push(...page.faces.map((f) => f.face_id));
    next = page.next_cursor;
  }
  return ids;
}

export function labelToast(r: ClusterLabelResult, kidName: string): { title: string; description: string } {
  const n = r.references_created;
  const title = `${n} ${n === 1 ? "face" : "faces"} added as ${n === 1 ? "a reference" : "references"} for ${kidName}`;
  const parts: string[] = [];
  if (r.duplicates > 0) parts.push(`${r.duplicates} duplicate${r.duplicates === 1 ? "" : "s"} skipped`);
  if (r.rescore) {
    parts.push(`Re-scoring ran: ${r.rescore.examined} examined, ${r.rescore.created + r.rescore.updated} matches changed`);
  } else {
    parts.push("Re-scoring did not run");
  }
  return { title, description: parts.join(". ") };
}
