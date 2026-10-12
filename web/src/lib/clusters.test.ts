import { describe, expect, it, vi } from "vitest";
import { dateRange, labelInvalidations, remainingFaceIds, faceCountLabel, groupsSummary, labelFaceIds, labelToast } from "./clusters";

describe("clusters helpers", () => {
  it("formats date ranges", () => {
    expect(dateRange(null, null)).toBe("");
    expect(dateRange("2026-10-01T12:00:00Z", "2026-10-01T15:00:00Z")).toBe(dateRange("2026-10-01T12:00:00Z", null));
    expect(dateRange("2026-10-01T12:00:00Z", "2026-10-09T12:00:00Z")).toContain("–");
  });
  it("summarises groups", () => {
    expect(groupsSummary(["a", "b"])).toBe("a, b");
    expect(groupsSummary(["a", "b", "c", "d", "e"])).toBe("a, b, c +2 more");
  });
  it("pluralises", () => {
    expect(faceCountLabel(1)).toBe("1 face");
    expect(faceCountLabel(4)).toBe("4 faces");
  });
  it("chooses face ids", () => {
    expect(labelFaceIds([1, 2, 3], new Set([2]), false)).toEqual([1, 3]);
    expect(labelFaceIds([1, 2, 3], new Set(), false)).toEqual([1, 2, 3]);
    expect(labelFaceIds([1, 2], new Set(), true)).toBeNull();
    expect(labelFaceIds([1, 2], new Set([1]), true)).toEqual([2]);
  });
  it("builds the label toast", () => {
    const t = labelToast(
      { cluster_id: 1, kid_id: 2, selected: 5, references_created: 4, duplicates: 1,
        rescore: { examined: 100, created: 3, updated: 2, saved: 0 } },
      "Noa",
    );
    expect(t.title).toBe("4 faces added as references for Noa");
    expect(t.description).toContain("1 duplicate skipped");
    expect(t.description).toContain("Re-scoring ran: 100 examined, 5 matches changed");
    expect(labelToast({ cluster_id: 1, kid_id: 2, selected: 1, references_created: 1, duplicates: 0, rescore: null }, "X").description)
      .toBe("Re-scoring did not run");
  });
});

describe("remainingFaceIds", () => {
  const page = (ids: number[], next: number | null) =>
    ({ id: 1, size: 5, label_status: "unlabeled", kid_id: null, next_cursor: next, faces: ids.map((face_id) => ({ face_id })) }) as never;
  it("fetches every page after the cursor so a partial label is complete", async () => {
    const fetchPage = vi.fn().mockResolvedValueOnce(page([3, 4], 4)).mockResolvedValueOnce(page([5], null));
    const rest = await remainingFaceIds(1, 2, fetchPage);
    expect(rest).toEqual([3, 4, 5]);
    expect(fetchPage.mock.calls.map((c) => c[1])).toEqual([2, 4]);
    expect(labelFaceIds([1, 2, ...rest], new Set([2]), true)).toEqual([1, 3, 4, 5]);
  });
  it("does nothing without a cursor", async () => {
    expect(await remainingFaceIds(1, null, vi.fn())).toEqual([]);
  });
});

describe("labelInvalidations", () => {
  it("covers review, photos, kids, activity and stats", () => {
    expect(labelInvalidations).toEqual(
      expect.arrayContaining([["review"], ["review", "count"], ["photos"], ["kids"], ["activity"], ["stats"]]),
    );
  });
});
