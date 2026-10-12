import { describe, expect, it } from "vitest";
import { EMPTY_FILTERS, filterQuery, flattenPages, groupByMonth, hasActiveFilters, invalidRange, mergeTabs, type Photo } from "./photos";

const p = (id: number, image_id: number): Photo => ({
  id,
  image_id,
  kid_id: id,
  kid_name: `k${id}`,
  group_id: 1,
  group_name: "G",
  sent_at: null,
  saved_at: "2026-10-01T00:00:00",
  file_url: `/api/images/${image_id}/file`,
  thumb_url: `/api/photos/${id}/thumb`,
  sender_name: null,
  kid_ids: [],
  kid_names: [],
  kids: [],
});

describe("photos helpers", () => {
  it("builds the query, covering the whole 'to' day", () => {
    expect(filterQuery({ kidId: "2", groupId: "", from: "2026-10-01", to: "2026-10-03" }, 40)).toEqual({
      kid_id: "2",
      group_id: undefined,
      from: "2026-10-01T00:00:00",
      to: "2026-10-03T23:59:59",
      cursor: 40,
    });
    expect(filterQuery(EMPTY_FILTERS).cursor).toBeUndefined();
  });
  it("detects active filters and bad ranges", () => {
    expect(hasActiveFilters(EMPTY_FILTERS)).toBe(false);
    expect(hasActiveFilters({ ...EMPTY_FILTERS, groupId: "1" })).toBe(true);
    expect(invalidRange({ ...EMPTY_FILTERS, from: "2026-10-05", to: "2026-10-01" })).toBe(true);
    expect(invalidRange({ ...EMPTY_FILTERS, from: "2026-10-01" })).toBe(false);
  });
  it("de-duplicates pages", () => {
    expect(
      flattenPages([
        { items: [p(1, 1), p(2, 2)], next_cursor: 2 },
        { items: [p(2, 2), p(3, 3)], next_cursor: null },
      ]).map((x) => x.id),
    ).toEqual([1, 2, 3]);
  });

  it("groups consecutive photos under local month headings", () => {
    const at = (id: number, sent: string | null): Photo => ({ ...p(id, id), sent_at: sent });
    const groups = groupByMonth(
      [at(1, "2026-10-15T12:00:00Z"), at(2, "2026-10-03T12:00:00Z"), at(3, "2026-09-20T12:00:00Z"), at(4, null)],
      "en-US",
    );
    expect(groups.map((g) => [g.key, g.label, g.items.map((x) => x.id)])).toEqual([
      ["2026-10", "October 2026", [1, 2]],
      ["2026-09", "September 2026", [3]],
      ["2026-10", "October 2026", [4]], // no sent_at: filed under saved_at (2026-10-01)
    ]);
    expect(groupByMonth([], "en-US")).toEqual([]);
  });

  it("merges counts into tabs, with zero for kids without photos", () => {
    const kids = [
      { id: 1, display_name: "Noa" },
      { id: 2, display_name: "Itai" },
    ];
    expect(mergeTabs(kids, { total: 5, kids: [{ kid_id: 2, kid_name: "Itai", count: 5 }] })).toEqual([
      { kidId: null, name: "All", count: 5 },
      { kidId: 1, name: "Noa", count: 0 },
      { kidId: 2, name: "Itai", count: 5 },
    ]);
    expect(mergeTabs(kids, undefined).map((t) => t.count)).toEqual([0, 0, 0]);
  });
});
