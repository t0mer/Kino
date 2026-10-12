import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeProvider } from "@/components/theme-provider";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { Kid } from "@/lib/kids";
import { createQueryClient } from "@/lib/query";
import type { ReviewItem } from "@/lib/review";
import { ReviewPage } from "./ReviewPage";

type Call = { method: string; url: string; body: any };
type Handler = (call: Call) => Response;
let routes: Record<string, Handler>;
let calls: Call[];

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const kid = (id: number, name: string, active = true): Kid => ({
  id, display_name: name, slug: name.toLowerCase(), birth_year: null, active, auto_save_enabled: false,
  threshold_offset: 0, group_ids: [], reference_count: 3, today: 0, last_7_days: 0, created_at: "2026-10-01T10:00:00",
});

const item = (id: number, over: Partial<ReviewItem> = {}): ReviewItem => ({
  match_id: id, face_id: 100 + id, image_id: 200 + id, kid_id: 1, kid_name: "נועה", score: 0.41, margin: 0.1,
  tier: "M", method: "face", group_id: 5, group_name: "כיתה ב", sent_at: "2026-10-10T08:00:00",
  bbox: { x: 10, y: 20, w: 30, h: 30 }, crop_url: `/api/crops/${100 + id}`, image_url: `/api/images/${200 + id}/file`,
  nearest_refs: [
    { reference_id: 1, similarity: 0.5, crop_url: "/api/references/1/crop" },
    { reference_id: 2, similarity: 0.4, crop_url: null },
  ],
  ...over,
});

const posts = () => calls.filter((c) => c.method === "POST");

beforeEach(() => {
  document.cookie = "kino_csrf=tok; path=/";
  calls = [];
  routes = {
    "GET /api/kids": () => json(200, [kid(1, "נועה"), kid(2, "Itai"), kid(3, "Gone", false)]),
    "GET /api/review": () => json(200, { items: [item(1), item(2), item(3)], next_cursor: null }),
    "POST /api/review/1": () => json(200, { match_id: 1, decision: "confirmed", kid_id: 1, changed: true }),
    "POST /api/review/2": () => json(200, { match_id: 2, decision: "rejected", kid_id: 1, changed: true }),
    "POST /api/review/3": () => json(200, { match_id: 3, decision: "rejected", kid_id: 1, changed: true }),
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
  if (!("ResizeObserver" in window)) {
    vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  }
});
afterEach(() => vi.unstubAllGlobals());

function renderPage() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <ThemeProvider>
        <TooltipProvider>
          <MemoryRouter>
            <ReviewPage />
          </MemoryRouter>
          <Toaster />
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

const currentId = () => screen.getByTestId("review-item").getAttribute("data-match-id");

describe("ReviewPage single view", () => {
  it("shows the item details with dir=auto names and the numbered kid list", async () => {
    renderPage();
    const card = await screen.findByTestId("review-item");
    expect(within(card).getByAltText("Face to review")).toHaveAttribute("src", "/api/crops/101");
    expect(within(card).getByAltText("Full photo")).toHaveAttribute("src", "/api/images/201/file");
    expect(within(card).getByText("Score 0.41")).toBeInTheDocument();
    expect(within(card).getByText("Tier M")).toBeInTheDocument();
    expect(within(card).getByText("כיתה ב")).toHaveAttribute("dir", "auto");
    expect(within(card).getAllByText("נועה")[0]).toHaveAttribute("dir", "auto");
    expect(within(card).getByAltText("Reference 1")).toBeInTheDocument();
    // Active kids only, numbered; the suggested kid cannot be chosen.
    expect(within(card).getByRole("button", { name: /Itai/ })).toBeEnabled();
    expect(within(card).queryByRole("button", { name: /Gone/ })).toBeNull();
    expect(within(card).getByRole("button", { name: /נועה/ })).toBeDisabled();
  });

  it("confirms with Y and advances to the next item", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId("review-item");
    await user.keyboard("y");
    await waitFor(() => expect(currentId()).toBe("2"));
    expect(posts()[0]).toMatchObject({ url: "/api/review/1", body: { action: "confirm" } });
  });

  it("reassigns with a number key", async () => {
    const user = userEvent.setup();
    routes["POST /api/review/1"] = () => json(200, { match_id: 1, decision: "confirmed", kid_id: 2, changed: true });
    renderPage();
    await screen.findByTestId("review-item");
    await user.keyboard("2");
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0]!.body).toEqual({ action: "reassign", kid_id: 2 });
    await waitFor(() => expect(currentId()).toBe("2"));
  });

  it("sends someone_else with O and reject with N; skip (S) sends nothing", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId("review-item");
    await user.keyboard("s");
    await waitFor(() => expect(currentId()).toBe("2"));
    expect(posts()).toHaveLength(0);
    await user.keyboard("o");
    await waitFor(() => expect(currentId()).toBe("3"));
    expect(posts()[0]).toMatchObject({ url: "/api/review/2", body: { action: "someone_else" } });
    await user.keyboard("n");
    await waitFor(() => expect(currentId()).toBe("1")); // the skipped one comes back last
    expect(posts()[1]).toMatchObject({ url: "/api/review/3", body: { action: "reject" } });
  });

  it("works with the buttons too", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: /Reject/ }));
    await waitFor(() => expect(posts()[0]).toMatchObject({ url: "/api/review/1", body: { action: "reject" } }));
  });

  it("ignores shortcuts while typing in an input and while a dialog is open", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId("review-item");
    const input = document.createElement("input");
    document.body.append(input);
    input.focus();
    await user.keyboard("y");
    input.remove();
    const dlg = document.createElement("div");
    dlg.setAttribute("role", "dialog");
    document.body.append(dlg);
    await user.keyboard("y");
    dlg.remove();
    expect(posts()).toHaveLength(0);
  });

  it("on 409 shows a toast and moves on to the next item", async () => {
    const user = userEvent.setup();
    routes["POST /api/review/1"] = () => json(409, { detail: "already decided" });
    renderPage();
    await screen.findByTestId("review-item");
    await user.keyboard("y");
    expect(await screen.findByText(/Already decided elsewhere/)).toBeInTheDocument();
    await waitFor(() => expect(currentId()).toBe("2"));
  });

  it("on 404 (raw file gone) shows a toast and moves on", async () => {
    const user = userEvent.setup();
    routes["POST /api/review/1"] = () => json(404, { detail: "gone" });
    renderPage();
    await screen.findByTestId("review-item");
    await user.keyboard("n");
    expect(await screen.findByText(/no longer available/)).toBeInTheDocument();
    await waitFor(() => expect(currentId()).toBe("2"));
  });

  it("stays on the item after a server error", async () => {
    const user = userEvent.setup();
    routes["POST /api/review/1"] = () => json(500, { detail: "boom" });
    renderPage();
    await screen.findByTestId("review-item");
    await user.keyboard("y");
    expect(await screen.findByText("Could not save the decision")).toBeInTheDocument();
    expect(currentId()).toBe("1");
  });

  it("shows the all-caught-up state when the queue is empty and after the last decision", async () => {
    routes["GET /api/review"] = () => json(200, { items: [item(1)], next_cursor: null });
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId("review-item");
    await user.keyboard("y");
    expect(await screen.findByText("All caught up")).toBeInTheDocument();
  });

  it("shows all caught up straight away with no items", async () => {
    routes["GET /api/review"] = () => json(200, { items: [], next_cursor: null });
    renderPage();
    expect(await screen.findByText("All caught up")).toBeInTheDocument();
  });

  it("shows an error state with retry", async () => {
    routes["GET /api/review"] = () => json(403, { detail: "db down" });
    renderPage();
    expect(await screen.findByText("Could not load the review queue")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Retry/ })).toBeInTheDocument();
  });

  it("filters by kid and method via query params", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId("review-item");
    await user.selectOptions(screen.getByLabelText("Filter by kid"), "2");
    await user.selectOptions(screen.getByLabelText("Filter by method"), "burst");
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes("kid_id=2") && c.url.includes("method=burst"))).toBe(true),
    );
  });

  it("prefetches the next page using the cursor when the queue runs low", async () => {
    let n = 0;
    routes["GET /api/review"] = () => {
      n += 1;
      return n === 1
        ? json(200, { items: [item(1), item(2)], next_cursor: 2 })
        : json(200, { items: [item(3)], next_cursor: null });
    };
    renderPage();
    await screen.findByTestId("review-item");
    await waitFor(() => expect(calls.some((c) => c.url.includes("cursor=2"))).toBe(true));
  });
});

describe("ReviewPage bulk mode", () => {
  it("confirms the selected items in one call and removes them", async () => {
    const user = userEvent.setup();
    routes["POST /api/review/bulk"] = (c) =>
      json(200, {
        results: c.body.match_ids.map((id: number) => ({
          match_id: id, status: id === 2 ? 409 : 200, detail: id === 2 ? "already decided" : null, result: null,
        })),
      });
    renderPage();
    await screen.findByTestId("review-item");
    await user.click(screen.getByRole("button", { name: /Bulk/ }));
    await user.click(await screen.findByLabelText("Select match 1"));
    await user.click(screen.getByLabelText("Select match 2"));
    expect(screen.getByText("2 selected")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Confirm selected/ }));
    await waitFor(() =>
      expect(posts().find((c) => c.url === "/api/review/bulk")?.body).toEqual({ match_ids: [1, 2], action: "confirm" }),
    );
    expect(await screen.findByText("1 done, 1 not applied")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId("bulk-item-1")).toBeNull());
    expect(screen.queryByTestId("bulk-item-2")).toBeNull(); // 409 means gone, too
    expect(screen.getByTestId("bulk-item-3")).toBeInTheDocument();
  });

  it("shift-click selects a range", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId("review-item");
    await user.click(screen.getByRole("button", { name: /Bulk/ }));
    await user.click(await screen.findByLabelText("Select match 1"));
    await user.keyboard("{Shift>}");
    await user.click(screen.getByLabelText("Select match 3"));
    await user.keyboard("{/Shift}");
    expect(screen.getByText("3 selected")).toBeInTheDocument();
  });

  it("select all then reject", async () => {
    const user = userEvent.setup();
    routes["POST /api/review/bulk"] = (c) =>
      json(200, { results: c.body.match_ids.map((id: number) => ({ match_id: id, status: 200, detail: null, result: null })) });
    renderPage();
    await screen.findByTestId("review-item");
    await user.click(screen.getByRole("button", { name: /Bulk/ }));
    await user.click(await screen.findByLabelText("Select all"));
    await user.click(screen.getByRole("button", { name: /Reject selected/ }));
    await waitFor(() =>
      expect(posts().find((c) => c.url === "/api/review/bulk")?.body).toEqual({ match_ids: [1, 2, 3], action: "reject" }),
    );
    expect(await screen.findByText("All caught up")).toBeInTheDocument();
  });

  it("does not react to single-item shortcuts in bulk mode", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId("review-item");
    await user.click(screen.getByRole("button", { name: /Bulk/ }));
    await screen.findByTestId("bulk-item-1");
    await user.keyboard("y");
    expect(posts()).toHaveLength(0);
  });
});
