/** Groups API (CLAUDE.md §11.7, §12) and the pure helpers the Groups page uses. */

import { api } from "./api";
import { groupKeys } from "./kids";

export { groupKeys };

export interface GroupItem {
  id: number;
  chat_id: string;
  name: string | null;
  slug: string | null;
  watched: boolean;
  kid_ids: number[];
  is_forward_destination: boolean;
  created_at: string;
  last_seen_at: string | null;
}

export interface GroupUpdate {
  watched?: boolean;
  kid_ids?: number[];
}

export interface GroupsRefreshed {
  imported: number;
  total: number;
}

export const groupsApi = {
  list: () => api.get<GroupItem[]>("/api/groups"),
  update: (id: number, body: GroupUpdate) => api.patch<GroupItem>(`/api/groups/${id}`, body),
  refresh: () => api.post<GroupsRefreshed>("/api/groups/refresh"),
};

/** Name to show for a group: its name, else its chat id. */
export function groupLabel(g: Pick<GroupItem, "name" | "chat_id">): string {
  return g.name?.trim() || g.chat_id;
}

/** Add or remove a kid id, keeping a stable ascending order. */
export function toggleKid(ids: number[], kidId: number): number[] {
  const next = ids.includes(kidId) ? ids.filter((i) => i !== kidId) : [...ids, kidId];
  return next.sort((a, b) => a - b);
}

/** Watched groups first, then by label (locale-aware). */
export function sortGroups(groups: GroupItem[]): GroupItem[] {
  return [...groups].sort(
    (a, b) => Number(b.watched) - Number(a.watched) || groupLabel(a).localeCompare(groupLabel(b)),
  );
}

export function refreshSummary(r: GroupsRefreshed): string {
  if (r.imported === 0) return `No new groups (${r.total} total)`;
  return `Imported ${r.imported} new group${r.imported === 1 ? "" : "s"} (${r.total} total)`;
}
