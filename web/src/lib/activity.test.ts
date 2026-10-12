import { describe, expect, it } from "vitest";
import {
  boxStyle,
  flattenPages,
  formatMs,
  hasActiveFilters,
  matchSummary,
  statusLabel,
  statusTone,
  type ActivityImage,
} from "./activity";

const img = (id: number): ActivityImage => ({
  id, group_id: null, group_name: null, sender: null, sent_at: null, received_at: "2026-10-11T08:00:00",
  status: "done", skip_reason: null, processing_ms: 10, error: null, face_count: 0, matches: {}, has_file: true,
});

describe("activity helpers", () => {
  it("flattens pages without duplicates", () => {
    const out = flattenPages([
      { items: [img(3), img(2)], next_cursor: 2 },
      { items: [img(2), img(1)], next_cursor: null },
    ]);
    expect(out.map((i) => i.id)).toEqual([3, 2, 1]);
  });
  it("labels and tones statuses", () => {
    expect(statusLabel("skipped", "duplicate")).toBe("skipped (duplicate)");
    expect(statusLabel("done")).toBe("done");
    expect(statusTone("failed")).toBe("destructive");
    expect(statusTone("queued")).toBe("secondary");
    expect(statusTone("skipped")).toBe("outline");
  });
  it("summarises matches", () => {
    expect(matchSummary({})).toBe("");
    expect(matchSummary({ review: 1, auto_saved: 2, dropped: 0 })).toBe("2 auto saved, 1 review");
  });
  it("formats durations", () => {
    expect(formatMs(null)).toBe("—");
    expect(formatMs(850)).toBe("850 ms");
    expect(formatMs(2500)).toBe("2.5 s");
  });
  it("detects filters", () => {
    expect(hasActiveFilters({ status: "", groupId: "" })).toBe(false);
    expect(hasActiveFilters({ status: "failed", groupId: "" })).toBe(true);
  });
  it("computes percent boxes", () => {
    expect(boxStyle([100, 50, 200, 100], { w: 1000, h: 500 })).toEqual({
      left: "10%", top: "10%", width: "20%", height: "20%",
    });
  });
});
