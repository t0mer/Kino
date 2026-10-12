import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createQueryClient } from "./query";
import { useJob } from "./useBackfill";

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const mkJob = (status: string) => ({ id: 5, kind: "backfill", status, group_id: 1, params: {}, total: 0, processed: 0, skipped: 0, errors: 0, error: null, created_at: "", started_at: null, finished_at: null });

let statuses: string[];
let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  client = createQueryClient();
  vi.useFakeTimers({ shouldAdvanceTime: false });
  statuses = ["queued", "running", "running", "done"];
  fetchMock = vi.fn(async () => json(200, mkJob(statuses.shift() ?? "done")));
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

let client = createQueryClient();
const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);

describe("useJob", () => {
  it("polls every 2 s and stops once the job is done", async () => {
    const { result } = renderHook(() => useJob(5), { wrapper });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.data?.status).toBe("queued");
    await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
    expect(result.current.data?.status).toBe("running");
    await act(async () => { await vi.advanceTimersByTimeAsync(4300); });
    expect(result.current.data?.status).toBe("done");
    const n = fetchMock.mock.calls.length;
    expect(n).toBe(4);
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000); });
    expect(fetchMock.mock.calls.length).toBe(n);
  });

  it("stops at failed", async () => {
    statuses = ["running", "failed"];
    renderHook(() => useJob(5), { wrapper });
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(fetchMock.mock.calls.length).toBe(2);
  });

  it("does not fetch without a job id", async () => {
    renderHook(() => useJob(null), { wrapper });
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
