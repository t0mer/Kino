import { describe, expect, it } from "vitest";
import { dateOnly, filterByDate, hasRefFilters, invalidRefRange, monthHistogram, monthLabel, sortRefs } from "./gallery";

const r = (id: number, photo_date: string | null) => ({ id, photo_date });

describe("gallery helpers", () => {
  it("dateOnly takes the date part", () => {
    expect(dateOnly("2026-10-05T10:00:00")).toBe("2026-10-05");
    expect(dateOnly(null)).toBe("");
  });

  it("filterByDate is inclusive and drops undated when bounded", () => {
    const refs = [r(1, "2026-10-01T00:00:00"), r(2, "2026-10-05T23:00:00"), r(3, "2026-11-01T00:00:00"), r(4, null)];
    expect(filterByDate(refs, "", "")).toHaveLength(4);
    expect(filterByDate(refs, "2026-10-05", "").map((x) => x.id)).toEqual([2, 3]);
    expect(filterByDate(refs, "", "2026-10-05").map((x) => x.id)).toEqual([1, 2]);
    expect(filterByDate(refs, "2026-10-01", "2026-10-05").map((x) => x.id)).toEqual([1, 2]);
  });

  it("sortRefs: newest first, undated last, id desc on ties, no mutation", () => {
    const refs = [r(1, "2026-01-01T00:00:00"), r(2, null), r(3, "2026-06-01T00:00:00"), r(4, "2026-06-01T00:00:00")];
    expect(sortRefs(refs).map((x) => x.id)).toEqual([4, 3, 1, 2]);
    expect(refs[0]!.id).toBe(1);
  });

  it("monthHistogram fills gaps across a year boundary and counts undated", () => {
    const h = monthHistogram([r(1, "2025-11-03T00:00:00"), r(2, "2025-11-20T00:00:00"), r(3, "2026-02-01T00:00:00"), r(4, null)]);
    expect(h.buckets).toEqual([
      { key: "2025-11", count: 2 },
      { key: "2025-12", count: 0 },
      { key: "2026-01", count: 0 },
      { key: "2026-02", count: 1 },
    ]);
    expect(h.undated).toBe(1);
    expect(h.max).toBe(2);
  });

  it("monthHistogram handles empty and all-undated", () => {
    expect(monthHistogram([])).toEqual({ buckets: [], undated: 0, max: 0 });
    expect(monthHistogram([r(1, null)])).toEqual({ buckets: [], undated: 1, max: 0 });
  });

  it("filters flags and range", () => {
    expect(hasRefFilters({ source: "", status: "", from: "", to: "" })).toBe(false);
    expect(hasRefFilters({ source: "auto", status: "", from: "", to: "" })).toBe(true);
    expect(invalidRefRange({ from: "2026-02-01", to: "2026-01-01" })).toBe(true);
    expect(invalidRefRange({ from: "", to: "2026-01-01" })).toBe(false);
  });

  it("monthLabel", () => {
    expect(monthLabel("2026-10", "en-US")).toBe("Oct 2026");
  });
});
