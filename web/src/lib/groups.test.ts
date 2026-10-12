import { describe, expect, it } from "vitest";
import { type GroupItem, groupLabel, refreshSummary, sortGroups, toggleKid } from "./groups";

const g = (id: number, name: string | null, watched: boolean): GroupItem => ({
  id,
  chat_id: `${id}@g.us`,
  name,
  slug: null,
  watched,
  kid_ids: [],
  is_forward_destination: false,
  created_at: "2026-10-01T00:00:00",
  last_seen_at: null,
});

describe("groups helpers", () => {
  it("labels a nameless group by chat id", () => {
    expect(groupLabel(g(1, null, false))).toBe("1@g.us");
    expect(groupLabel(g(1, "  ", false))).toBe("1@g.us");
    expect(groupLabel(g(1, "Class", false))).toBe("Class");
  });
  it("toggles kid ids in order", () => {
    expect(toggleKid([3, 1], 2)).toEqual([1, 2, 3]);
    expect(toggleKid([1, 2], 1)).toEqual([2]);
  });
  it("sorts watched first then by name", () => {
    const out = sortGroups([g(1, "b", false), g(2, "z", true), g(3, "a", false)]);
    expect(out.map((x) => x.id)).toEqual([2, 3, 1]);
  });
  it("summarises refresh", () => {
    expect(refreshSummary({ imported: 0, total: 4 })).toBe("No new groups (4 total)");
    expect(refreshSummary({ imported: 1, total: 4 })).toBe("Imported 1 new group (4 total)");
    expect(refreshSummary({ imported: 2, total: 4 })).toBe("Imported 2 new groups (4 total)");
  });
});
