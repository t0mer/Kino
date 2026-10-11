import { describe, expect, it } from "vitest";
import { formatBadge, formatDateTime, formatDuration, formatRelative, parseApiDate } from "./format";

describe("parseApiDate", () => {
  it("reads naive backend timestamps as UTC", () => {
    expect(parseApiDate("2026-10-11T08:30:00")?.toISOString()).toBe("2026-10-11T08:30:00.000Z");
    expect(parseApiDate("2026-10-11 08:30:00")?.toISOString()).toBe("2026-10-11T08:30:00.000Z");
  });
  it("respects explicit offsets", () => {
    expect(parseApiDate("2026-10-11T08:30:00+03:00")?.toISOString()).toBe("2026-10-11T05:30:00.000Z");
    expect(parseApiDate("2026-10-11T08:30:00Z")?.toISOString()).toBe("2026-10-11T08:30:00.000Z");
  });
  it("returns null for empty or invalid input", () => {
    expect(parseApiDate(null)).toBeNull();
    expect(parseApiDate("")).toBeNull();
    expect(parseApiDate("not a date")).toBeNull();
  });
});

describe("formatters", () => {
  it("formats date-time in a given zone", () => {
    expect(formatDateTime("2026-10-11T08:30:00", "en-GB", "UTC")).toBe("11 Oct 2026, 08:30");
    expect(formatDateTime(null)).toBe("—");
  });

  it("formats relative times", () => {
    const now = new Date("2026-10-11T12:00:00Z");
    expect(formatRelative("2026-10-11T11:59:50", now, "en")).toBe("just now");
    expect(formatRelative("2026-10-11T11:55:00", now, "en")).toBe("5 minutes ago");
    expect(formatRelative("2026-10-11T09:00:00", now, "en")).toBe("3 hours ago");
    expect(formatRelative("2026-10-10T12:00:00", now, "en")).toBe("yesterday");
    expect(formatRelative("2026-10-11T14:00:00", now, "en")).toBe("in 2 hours");
  });

  it("caps badge counts", () => {
    expect(formatBadge(0)).toBe("0");
    expect(formatBadge(7)).toBe("7");
    expect(formatBadge(120)).toBe("99+");
  });

  it("formats durations", () => {
    expect(formatDuration(42)).toBe("42s");
    expect(formatDuration(200)).toBe("3m 20s");
    expect(formatDuration(900)).toBe("15m");
    expect(formatDuration(3900)).toBe("1h 5m");
  });
});
