import { QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeProvider } from "@/components/theme-provider";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { ScoreFace, ScoreResult } from "@/lib/dryrun";
import { createQueryClient } from "@/lib/query";
import { TestPage } from "./TestPage";

type Call = { method: string; url: string; body: unknown };
let routes: Record<string, (c: Call) => Response>;
let calls: Call[];
const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const kid = (id: number, name: string, over = {}) => ({
  id, display_name: name, slug: name.toLowerCase(), birth_year: null, active: true, auto_save_enabled: false,
  threshold_offset: 0, group_ids: [], reference_count: 12, today: 0, last_7_days: 0, created_at: "2026-10-01T00:00:00", ...over,
});
const settings = {
  forward: {}, digest: {}, retention: {}, embedder: "auraface", models: [], version: "1",
  thresholds: { L: { auto: 0.45, review: 0.32 }, M: { auto: 0.5, review: 0.35 }, S: { review: 0.4 }, prior_bonus: 0.03, unmapped_penalty: 0.05, margin_min: 0.08, auto_floor: 0.3 },
};
const face = (id: number, over: Partial<ScoreFace> = {}): ScoreFace => ({
  face_id: id, image_id: id, crop_url: `/api/crops/${id}`, score: 0.5, margin: 0.1, tier: "L", kid_id: 1, kid_name: "Noa",
  group_id: 10, group_name: "כיתה ב", sent_at: "2026-10-10T10:00:00", reason: "ok", auto_save_disabled: false, ...over,
});
const result = (over: Partial<ScoreResult> = {}): ScoreResult => ({
  from: "", to: "", examined: 5, truncated: false, per_kid: [],
  buckets: {
    would_auto_save: { count: 1, items: [face(1)] },
    would_review: { count: 100, items: [face(2, { score: 0.4 })] },
    would_drop: { count: 0, items: [] },
  },
  ...over,
});

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  document.cookie = "kino_csrf=tok; path=/";
  calls = [];
  routes = {
    "GET /api/settings": () => json(200, settings),
    "GET /api/kids": () => json(200, [kid(1, "Noa"), kid(2, "איתי", { reference_count: 0 })]),
    "POST /api/test/score": () => json(200, result()),
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const method = (init.method ?? "GET").toUpperCase();
      const call = { method, url, body: init.body ? JSON.parse(String(init.body)) : undefined };
      calls.push(call);
      const h = routes[`${method} ${url.split("?")[0]}`];
      return h ? h(call) : json(404, { detail: "not found" });
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

function renderPage() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <ThemeProvider>
        <TooltipProvider>
          <MemoryRouter>
            <TestPage />
          </MemoryRouter>
          <Toaster />
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}
const scoreCalls = () => calls.filter((c) => c.url === "/api/test/score");

describe("TestPage", () => {
  it("scores with the stored thresholds and shows counts, crops and the capped note", async () => {
    renderPage();
    expect(await screen.findByRole("region", { name: "Would review" })).toBeInTheDocument();
    const review = screen.getByRole("region", { name: "Would review" });
    expect(within(review).getByLabelText("100 faces")).toBeInTheDocument();
    expect(within(review).getByText("Showing 1 of 100")).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Would auto-save" })).getByRole("button", { name: /Face 1/ })).toBeInTheDocument();
    const body = scoreCalls()[0]!.body as { thresholds: { L: { auto: number } }; kid_ids?: number[]; from: string };
    expect(body.thresholds.L.auto).toBe(0.45);
    expect(body.kid_ids).toBeUndefined();
    expect(body.from).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00\.000Z$/);
  });

  it("a slider change triggers one debounced score call with the new value", async () => {
    renderPage();
    await screen.findByRole("region", { name: "Would review" });
    expect(scoreCalls()).toHaveLength(1);
    routes["POST /api/test/score"] = () =>
      json(200, result({ buckets: { would_auto_save: { count: 7, items: [face(9)] }, would_review: { count: 0, items: [] }, would_drop: { count: 0, items: [] } } }));
    const thumb = screen.getAllByRole("slider")[2]!; // L auto, L review, M auto, ...
    thumb.focus();
    for (let i = 0; i < 3; i++) fireEvent.keyDown(thumb, { key: "ArrowRight" });
    await waitFor(() => expect(scoreCalls()).toHaveLength(2), { timeout: 2000 });
    await new Promise((r) => setTimeout(r, 450));
    expect(scoreCalls()).toHaveLength(2);
    expect((scoreCalls()[1]!.body as { thresholds: { M: { auto: number } } }).thresholds.M.auto).toBeCloseTo(0.53, 5);
    expect(within(await screen.findByRole("region", { name: "Would auto-save" })).getByLabelText("7 faces")).toBeInTheDocument();
  });

  it("applies thresholds after a confirm summarising the change; disabled when unchanged", async () => {
    routes["PUT /api/settings/thresholds"] = (c) => json(200, { ...(c.body as object), auto_floor: 0.3 });
    renderPage();
    const applyBtn = await screen.findByRole("button", { name: "Apply thresholds" });
    expect(applyBtn).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Tier L review"), { target: { value: "0.3" } });
    expect(applyBtn).toBeEnabled();
    await userEvent.click(applyBtn);
    const dlg = await screen.findByRole("dialog");
    expect(within(dlg).getByText("Tier L review")).toBeInTheDocument();
    expect(within(dlg).getByText("0.32 → 0.30")).toBeInTheDocument();
    await userEvent.click(within(dlg).getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PUT")).toBe(true));
    expect((calls.find((c) => c.method === "PUT")!.body as { L: { review: number } }).L.review).toBe(0.3);
    await waitFor(() => expect(screen.getByRole("button", { name: "Apply thresholds" })).toBeDisabled());
  });

  it("shows server validation errors inline", async () => {
    routes["PUT /api/settings/thresholds"] = () => json(422, { detail: [{ loc: ["body", "L", "auto"], msg: "Value error, nope" }] });
    renderPage();
    await screen.findByRole("region", { name: "Would review" });
    fireEvent.change(screen.getByLabelText("Tier L review"), { target: { value: "0.4" } });
    await userEvent.click(screen.getByRole("button", { name: "Apply thresholds" }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Apply" }));
    expect(await screen.findByText("nope")).toBeInTheDocument();
  });

  it("blocks invalid thresholds locally and does not re-score", async () => {
    renderPage();
    await screen.findByRole("region", { name: "Would review" });
    fireEvent.change(screen.getByLabelText("Tier L auto-save"), { target: { value: "0.2" } });
    expect(await screen.findByText(/at least the review value/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Apply thresholds" })).toBeDisabled();
    await new Promise((r) => setTimeout(r, 450));
    expect(scoreCalls()).toHaveLength(1);
  });

  it("enables auto-save for the selected kid after a confirm", async () => {
    routes["PATCH /api/kids/1"] = () => json(200, kid(1, "Noa", { auto_save_enabled: true }));
    renderPage();
    await screen.findByRole("region", { name: "Would review" });
    expect(screen.queryByRole("button", { name: /Enable auto-save/ })).toBeNull();
    await userEvent.selectOptions(screen.getByLabelText("Kid"), "1");
    await userEvent.click(await screen.findByRole("button", { name: /Enable auto-save for/ }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Enable auto-save" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ auto_save_enabled: true }));
    await waitFor(() => expect((scoreCalls().at(-1)!.body as { kid_ids?: number[] }).kid_ids).toEqual([1]));
  });

  it("flags a crop as wrong", async () => {
    routes["POST /api/test/flag"] = () => json(200, { face_id: 1, kid_id: 1, action: "review", changed: true });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /Face 1,/ }));
    const dlg = await screen.findByRole("dialog");
    await userEvent.click(within(dlg).getByRole("button", { name: "Send to review" }));
    await waitFor(() => expect(calls.find((c) => c.url === "/api/test/flag")?.body).toEqual({ face_id: 1, kid_id: 1, action: "review" }));
    expect(await screen.findByText("Sent to the review queue.")).toBeInTheDocument();
  });

  it("re-scores the last N days after a confirm", async () => {
    routes["POST /api/rescore"] = () => json(200, { examined: 40, created: 3, updated: 1, unchanged: 36, protected: 0, saved: 2, retired: 0 });
    renderPage();
    await screen.findByRole("region", { name: "Would review" });
    await userEvent.click(screen.getByRole("button", { name: /Re-score last 14 days/ }));
    await userEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Re-score" }));
    await waitFor(() => expect(calls.find((c) => c.url === "/api/rescore")?.body).toEqual({ days: 14 }));
    expect(await screen.findByText(/40 faces examined/)).toBeInTheDocument();
  });

  it("shows the empty state, and an enroll link for a kid without references", async () => {
    routes["POST /api/test/score"] = () => json(200, result({ examined: 0, buckets: { would_auto_save: { count: 0, items: [] }, would_review: { count: 0, items: [] }, would_drop: { count: 0, items: [] } } }));
    renderPage();
    expect(await screen.findByText("No faces in this range")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Kid"), "2");
    const link = await screen.findByRole("link", { name: "Enroll faces" });
    expect(link).toHaveAttribute("href", "/kids/2/enroll");
    expect(screen.getByRole("option", { name: "איתי" })).toHaveAttribute("dir", "auto");
  });

  it("shows a retryable error when scoring fails", async () => {
    routes["POST /api/test/score"] = () => json(500, { detail: "boom" });
    renderPage();
    expect(await screen.findByText("Could not score")).toBeInTheDocument();
    routes["POST /api/test/score"] = () => json(200, result());
    await userEvent.click(screen.getByRole("button", { name: /Retry/ }));
    expect(await screen.findByRole("region", { name: "Would review" })).toBeInTheDocument();
  });
});
