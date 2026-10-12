import { describe, expect, it } from "vitest";
import { type Job, activeJobsByGroup, jobFraction, jobRefetchInterval, jobSummary, parseDays } from "./backfill";

const job = (over: Partial<Job> = {}): Job => ({
  id: 1, kind: "backfill", status: "running", group_id: 10, params: { days: 30 }, total: 0, processed: 0,
  skipped: 0, errors: 0, error: null, created_at: "2026-10-10T00:00:00", started_at: null, finished_at: null, ...over,
});

describe("parseDays", () => {
  it("accepts whole numbers 1..90 only", () => {
    expect(parseDays("30")).toBe(30);
    expect(parseDays(" 1 ")).toBe(1);
    expect(parseDays("90")).toBe(90);
    for (const bad of ["0", "91", "", "-3", "2.5", "abc", "1e1"]) expect(parseDays(bad)).toBeNull();
  });
});

describe("jobRefetchInterval", () => {
  it("polls while queued/running or unknown, stops at done/failed", () => {
    expect(jobRefetchInterval(undefined)).toBe(2000);
    expect(jobRefetchInterval(job({ status: "queued" }))).toBe(2000);
    expect(jobRefetchInterval(job({ status: "running" }))).toBe(2000);
    expect(jobRefetchInterval(job({ status: "done" }))).toBe(false);
    expect(jobRefetchInterval(job({ status: "failed" }))).toBe(false);
  });
});

describe("activeJobsByGroup", () => {
  it("keeps the newest active backfill per group", () => {
    const m = activeJobsByGroup([
      job({ id: 5, group_id: 1 }),
      job({ id: 7, group_id: 1, status: "queued" }),
      job({ id: 9, group_id: 1, status: "done" }),
      job({ id: 3, group_id: 2, status: "failed" }),
      job({ id: 4, group_id: null }),
      job({ id: 8, group_id: 3, kind: "other" }),
    ]);
    expect(m).toEqual({ 1: 7 });
    expect(activeJobsByGroup(undefined)).toEqual({});
  });
});

describe("jobFraction / jobSummary", () => {
  it("is indeterminate until the total is known and caps at 1", () => {
    expect(jobFraction(job())).toBeNull();
    expect(jobFraction(job({ total: 10, processed: 3, skipped: 1, errors: 1 }))).toBe(0.5);
    expect(jobFraction(job({ total: 2, processed: 5 }))).toBe(1);
  });
  it("describes each state", () => {
    expect(jobSummary(job({ status: "queued" }))).toBe("Queued, waiting for a worker");
    expect(jobSummary(job())).toBe("Fetching message history");
    expect(jobSummary(job({ total: 12, processed: 4, skipped: 1, errors: 2 }))).toBe("4 of 12 photos imported · 1 skipped · 2 errors");
    expect(jobSummary(job({ status: "done", total: 1, processed: 1 }))).toBe("1 of 1 photo imported");
  });
});
