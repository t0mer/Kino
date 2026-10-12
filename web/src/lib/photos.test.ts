import { describe, expect, it } from "vitest";
import { EMPTY_FILTERS, filterQuery, flattenPages, hasActiveFilters, invalidRange, type Photo, siblingsOf } from "./photos";

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
  it("finds siblings and de-duplicates pages", () => {
    const all = [p(1, 9), p(2, 9), p(3, 4)];
    expect(siblingsOf(all[0]!, all).map((x) => x.id)).toEqual([1, 2]);
    expect(
      flattenPages([
        { items: [p(1, 1), p(2, 2)], next_cursor: 2 },
        { items: [p(2, 2), p(3, 3)], next_cursor: null },
      ]).map((x) => x.id),
    ).toEqual([1, 2, 3]);
  });
});
