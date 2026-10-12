import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeProvider } from "@/components/theme-provider";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { createQueryClient } from "@/lib/query";
import { GroupsPage } from "@/pages/GroupsPage";
import { ClustersPage } from "@/pages/ClustersPage";

type Call = { method: string; url: string };
let routes: Record<string, (c: Call) => Response>;
let calls: Call[];
const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const grp = (id: number, name: string, watched = true) => ({
  id, chat_id: `${id}@g.us`, name, slug: null, watched, kid_ids: [], is_forward_destination: false,
  created_at: "2026-10-01T00:00:00", last_seen_at: "2026-10-10T00:00:00",
});
const job = (id: number, status: string, over: Record<string, unknown> = {}) => ({
  id, kind: "backfill", status, group_id: 10, params: { days: 30 }, total: 0, processed: 0, skipped: 0, errors: 0,
  error: null, created_at: "2026-10-12T00:00:00", started_at: null, finished_at: null, ...over,
});

beforeEach(() => {
  document.cookie = "kino_csrf=tok; path=/";
  calls = [];
  routes = {
    "GET /api/groups": () => json(200, [grp(10, "Class 3B"), grp(11, "Football", false)]),
    "GET /api/kids": () => json(200, []),
    "GET /api/jobs": () => json(200, []),
    "GET /api/clusters": () => json(200, { items: [], next_cursor: null }),
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const method = (init.method ?? "GET").toUpperCase();
      const call = { method, url };
      calls.push(call);
      const h = routes[`${method} ${url.split("?")[0]}`];
      return h ? h(call) : json(404, { detail: "not found" });
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

function renderUi(page: "groups" | "clusters") {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <ThemeProvider>
        <TooltipProvider>
          <MemoryRouter>{page === "groups" ? <GroupsPage /> : <ClustersPage />}</MemoryRouter>
          <Toaster />
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}
const starts = () => calls.filter((c) => c.method === "POST" && c.url.includes("/backfill"));

describe("Groups backfill", () => {
  it("starts a backfill with the chosen days and shows progress to done", async () => {
    let polls = 0;
    routes["POST /api/groups/10/backfill"] = () => json(202, { job_id: 7 });
    routes["GET /api/jobs/7"] = () => {
      polls += 1;
      return json(200, polls === 1 ? job(7, "running", { total: 10, processed: 3, skipped: 1 }) : job(7, "done", { total: 10, processed: 9, skipped: 1 }));
    };
    const user = userEvent.setup();
    renderUi("groups");
    const row = await screen.findByTestId("group-10");
    await user.click(within(row).getByRole("button", { name: "Backfill Class 3B" }));
    const days = await screen.findByLabelText("Days to fetch");
    expect(days).toHaveValue("30");
    await user.clear(days);
    await user.type(days, "91");
    expect(screen.getByRole("button", { name: /Start backfill/ })).toBeDisabled();
    await user.clear(days);
    await user.type(days, "14");
    await user.click(screen.getByRole("button", { name: /Start backfill/ }));
    await waitFor(() => expect(starts().map((c) => c.url)).toEqual(["/api/groups/10/backfill?days=14"]));
    expect(await within(row).findByText("3 of 10 photos imported · 1 skipped")).toBeInTheDocument();
    expect(await within(row).findByText(/Backfill finished: 9 of 10 photos imported/, {}, { timeout: 4000 })).toBeInTheDocument();
  });

  it("attaches to the running job on 409", async () => {
    routes["POST /api/groups/10/backfill"] = () => json(409, { detail: "already running" });
    routes["GET /api/jobs"] = () => json(200, calls.some((c) => c.method === "POST") ? [job(4, "running", { total: 5, processed: 2 })] : []);
    routes["GET /api/jobs/4"] = () => json(200, job(4, "running", { total: 5, processed: 2 }));
    const user = userEvent.setup();
    renderUi("groups");
    const row = await screen.findByTestId("group-10");
    await user.click(within(row).getByRole("button", { name: "Backfill Class 3B" }));
    await user.click(await screen.findByRole("button", { name: /Start backfill/ }));
    expect(await within(row).findByText("2 of 5 photos imported")).toBeInTheDocument();
    expect(await screen.findByText("A backfill is already running for this group")).toBeInTheDocument();
  });

  it("shows a friendly message on 404 and on network errors, keeping the dialog open", async () => {
    routes["POST /api/groups/10/backfill"] = () => json(404, { detail: "group not found" });
    const user = userEvent.setup();
    renderUi("groups");
    const row = await screen.findByTestId("group-10");
    await user.click(within(row).getByRole("button", { name: "Backfill Class 3B" }));
    await user.click(await screen.findByRole("button", { name: /Start backfill/ }));
    expect(await screen.findByText("That group no longer exists")).toBeInTheDocument();
    routes["POST /api/groups/10/backfill"] = () => { throw new TypeError("net"); };
    await user.click(screen.getByRole("button", { name: /Start backfill/ }));
    expect(await screen.findByText("Could not start the backfill")).toBeInTheDocument();
    expect(screen.getByLabelText("Days to fetch")).toBeInTheDocument();
  });

  it("shows the sanitised error of a failed job", async () => {
    routes["GET /api/jobs"] = () => json(200, [job(9, "running")]);
    routes["GET /api/jobs/9"] = () => json(200, job(9, "failed", { error: "OpenWA unavailable" }));
    renderUi("groups");
    const row = await screen.findByTestId("group-10");
    expect(await within(row).findByText(/Backfill failed: OpenWA unavailable/)).toBeInTheDocument();
  });

  it("picks up a running job found on page load", async () => {
    routes["GET /api/jobs"] = () => json(200, [job(3, "running", { total: 8, processed: 4 })]);
    routes["GET /api/jobs/3"] = () => json(200, job(3, "running", { total: 8, processed: 4 }));
    renderUi("groups");
    const row = await screen.findByTestId("group-10");
    expect(await within(row).findByText("4 of 8 photos imported")).toBeInTheDocument();
  });
});

describe("Clusters empty state import", () => {
  it("lists watched groups, starts a 30 day backfill and offers Find clusters now when done", async () => {
    routes["POST /api/groups/10/backfill"] = () => json(202, { job_id: 2 });
    routes["GET /api/jobs/2"] = () => json(200, job(2, "done", { total: 4, processed: 4 }));
    routes["POST /api/clusters/run"] = () => json(202, { status: "queued", job_id: "j" });
    const user = userEvent.setup();
    renderUi("clusters");
    const select = await screen.findByLabelText("Group to import from");
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["Class 3B"]);
    await user.click(screen.getByRole("button", { name: "Import last 30 days" }));
    await waitFor(() => expect(starts().map((c) => c.url)).toEqual(["/api/groups/10/backfill?days=30"]));
    expect(await screen.findByText(/Backfill finished/)).toBeInTheDocument();
    const buttons = screen.getAllByRole("button", { name: "Find clusters now" });
    expect(buttons).toHaveLength(2);
    await user.click(buttons[1]!);
    await waitFor(() => expect(calls.some((c) => c.url === "/api/clusters/run")).toBe(true));
  });

  it("falls back to all groups with a note when none is watched", async () => {
    routes["GET /api/groups"] = () => json(200, [grp(10, "Class 3B", false), grp(11, "Football", false)]);
    renderUi("clusters");
    const select = await screen.findByLabelText("Group to import from");
    expect(within(select).getAllByRole("option")).toHaveLength(2);
    expect(screen.getByText(/No group is watched yet/)).toBeInTheDocument();
  });
});
