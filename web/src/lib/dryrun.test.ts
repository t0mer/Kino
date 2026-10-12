import { describe, expect, it } from "vitest";
import {
  cappedNote,
  defaultRange,
  invalidRange,
  parseDays,
  scoreBody,
  thresholdChanges,
  withValue,
} from "./dryrun";
import { DEFAULT_THRESHOLDS, validateThresholds } from "./settings";

describe("dryrun helpers", () => {
  it("defaultRange covers the last 14 days in local dates", () => {
    expect(defaultRange(new Date(2026, 9, 12))).toEqual({ from: "2026-09-28", to: "2026-10-12" });
  });
  it("invalidRange only when from is after to", () => {
    expect(invalidRange("2026-10-02", "2026-10-01")).toBe(true);
    expect(invalidRange("2026-10-01", "2026-10-01")).toBe(false);
    expect(invalidRange("", "2026-10-01")).toBe(false);
  });
  it("parseDays accepts whole numbers 1..365", () => {
    expect(parseDays("14")).toBe(14);
    expect(parseDays("0")).toBeNull();
    expect(parseDays("366")).toBeNull();
    expect(parseDays("1.5")).toBeNull();
    expect(parseDays("")).toBeNull();
  });
  it("cappedNote only when items are fewer than the count", () => {
    expect(cappedNote({ count: 5, items: new Array(5) as never[] })).toBeNull();
    expect(cappedNote({ count: 213, items: new Array(60) as never[] })).toBe("Showing 60 of 213");
  });
  it("scoreBody maps kid and day range to the API shape", () => {
    const b = scoreBody({ kidId: "3", from: "2026-10-01", to: "2026-10-12", thresholds: DEFAULT_THRESHOLDS });
    expect(b.kid_ids).toEqual([3]);
    expect(b.from).toBe("2026-10-01T00:00:00");
    expect(b.to).toBe("2026-10-12T23:59:59");
    expect(scoreBody({ kidId: "", from: "", to: "", thresholds: DEFAULT_THRESHOLDS }).kid_ids).toBeUndefined();
  });
  it("thresholdChanges lists only differing tier values; withValue rounds", () => {
    const d = withValue(DEFAULT_THRESHOLDS, "M.auto", 0.5400000001);
    expect(d.M.auto).toBe(0.54);
    expect(thresholdChanges(DEFAULT_THRESHOLDS, d)).toEqual([
      { path: "M.auto", label: "Tier M auto-save", from: 0.5, to: 0.54 },
    ]);
    expect(thresholdChanges(DEFAULT_THRESHOLDS, DEFAULT_THRESHOLDS)).toEqual([]);
  });
  it("threshold validation: auto below review and below the floor are rejected", () => {
    expect(validateThresholds(withValue(DEFAULT_THRESHOLDS, "L.auto", 0.2), 0.3)["L.auto"]).toBeTruthy();
    expect(validateThresholds(DEFAULT_THRESHOLDS, 0.3)).toEqual({});
  });
});
