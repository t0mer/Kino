import { describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import {
  bulkSettled,
  bulkSummary,
  goneMessage,
  isGone,
  needsPrefetch,
  queueOrder,
  type ReviewItem,
  selectRange,
  shortcutFor,
  shouldIgnoreKey,
  decisionInvalidations,
  invalidateKeys,
  withSkipped,
} from "./review";
import { QueryClient } from "@tanstack/react-query";

const item = (id: number): ReviewItem => ({
  match_id: id, face_id: id, image_id: id, kid_id: 1, kid_name: "k", score: 0.4, margin: null, tier: "M",
  method: "face", group_id: null, group_name: null, sent_at: null, bbox: { x: 0, y: 0, w: 1, h: 1 },
  crop_url: null, image_url: null, nearest_refs: [],
});

describe("shortcutFor", () => {
  it("maps letters case-insensitively", () => {
    expect(shortcutFor("y", [], 1)).toEqual({ type: "decide", action: "confirm" });
    expect(shortcutFor("N", [], 1)).toEqual({ type: "decide", action: "reject" });
    expect(shortcutFor("o", [], 1)).toEqual({ type: "decide", action: "someone_else" });
    expect(shortcutFor("s", [], 1)).toEqual({ type: "skip" });
    expect(shortcutFor("x", [], 1)).toBeNull();
  });
  it("maps digits to the numbered kid, skipping the current kid and unknown numbers", () => {
    expect(shortcutFor("2", [10, 20, 30], 10)).toEqual({ type: "reassign", kidId: 20 });
    expect(shortcutFor("1", [10, 20], 10)).toBeNull();
    expect(shortcutFor("5", [10, 20], 10)).toBeNull();
    expect(shortcutFor("0", [10], 99)).toBeNull();
  });
});

describe("shouldIgnoreKey", () => {
  it("ignores typing, modifiers and open dialogs", () => {
    const input = document.createElement("input");
    expect(shouldIgnoreKey({ key: "y", target: input })).toBe(true);
    expect(shouldIgnoreKey({ key: "y", ctrlKey: true, target: document.body })).toBe(true);
    expect(shouldIgnoreKey({ key: "y", target: document.body })).toBe(false);
    const d = document.createElement("div");
    d.setAttribute("role", "dialog");
    document.body.append(d);
    expect(shouldIgnoreKey({ key: "y", target: document.body })).toBe(true);
    d.remove();
  });
  it("ignores auto-repeat keydown events", () => {
    expect(shouldIgnoreKey({ key: "y", repeat: true, target: document.body })).toBe(true);
    expect(shouldIgnoreKey(new KeyboardEvent("keydown", { key: "y", repeat: true }))).toBe(true);
    expect(shouldIgnoreKey(new KeyboardEvent("keydown", { key: "y" }))).toBe(false);
  });
});

describe("queueOrder", () => {
  const items = [item(1), item(2), item(3), item(4)];
  it("drops decided and moves skipped to the back in skip order", () => {
    const order = queueOrder(items, new Set([2]), [1]).map((i) => i.match_id);
    expect(order).toEqual([3, 4, 1]);
    expect(queueOrder(items, new Set(), [3, 1]).map((i) => i.match_id)).toEqual([2, 4, 3, 1]);
  });
  it("cycles when only skipped items remain", () => {
    const s = withSkipped(withSkipped([], 1), 2);
    expect(queueOrder([item(1), item(2)], new Set(), s).map((i) => i.match_id)).toEqual([1, 2]);
    expect(withSkipped(s, 1)).toEqual([2, 1]);
  });
});

describe("misc", () => {
  it("prefetch threshold", () => {
    expect(needsPrefetch(3, true)).toBe(true);
    expect(needsPrefetch(30, true)).toBe(false);
    expect(needsPrefetch(0, false)).toBe(false);
  });
  it("gone errors", () => {
    expect(isGone(new ApiError(409, "x"))).toBe(true);
    expect(isGone(new ApiError(404, "x"))).toBe(true);
    expect(isGone(new ApiError(500, "x"))).toBe(false);
    expect(goneMessage(new ApiError(409, "x"))).toMatch(/already decided/i);
    expect(goneMessage(new ApiError(404, "x"))).toMatch(/no longer available/i);
  });
  it("bulk helpers", () => {
    const r = [
      { match_id: 1, status: 200, detail: null, result: null },
      { match_id: 2, status: 409, detail: "d", result: null },
      { match_id: 3, status: 502, detail: "d", result: null },
    ];
    expect(bulkSettled(r)).toEqual([1, 2]);
    expect(bulkSummary(r)).toMatchObject({ ok: 1 });
    expect(bulkSummary(r).failed.map((f) => f.match_id)).toEqual([2, 3]);
  });
  it("selectRange toggles or selects a range", () => {
    const order = [1, 2, 3, 4, 5];
    expect([...selectRange(order, new Set(), 2, null)]).toEqual([2]);
    expect([...selectRange(order, new Set([2]), 2, null)]).toEqual([]);
    expect([...selectRange(order, new Set([2]), 4, 2)].sort()).toEqual([2, 3, 4]);
  });
});

describe("decision invalidations", () => {
  it("refreshes count, photos, kids, activity and stats", () => {
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");
    invalidateKeys(qc, decisionInvalidations);
    const keys = spy.mock.calls.map((c) => (c[0] as { queryKey: unknown[] }).queryKey);
    expect(keys).toEqual(
      expect.arrayContaining([["review", "count"], ["photos"], ["kids"], ["activity"], ["stats"]]),
    );
  });
});
