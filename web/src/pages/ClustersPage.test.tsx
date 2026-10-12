import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeProvider } from "@/components/theme-provider";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { createQueryClient } from "@/lib/query";
import { ClustersPage } from "./ClustersPage";

type Call = { method: string; url: string; body: unknown };
let routes: Record<string, (c: Call) => Response>;
let calls: Call[];
const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const card = (id: number, size = 5) => ({
  id, size, label_status: "unlabeled", kid_id: null,
  sample_crop_urls: [1, 2, 3, 4, 5, 6].map((i) => `/api/crops/${id * 100 + i}`),
  groups: ["Class 3B"], date_from: "2026-10-01T10:00:00", date_to: "2026-10-09T10:00:00",
});
const face = (face_id: number) => ({ face_id, crop_url: `/api/crops/${face_id}`, quality: 0.7, size_px: 60, group_name: "Class 3B", sent_at: "2026-10-01T10:00:00" });
const kid = (id: number, display_name: string, active = true) => ({
  id, display_name, slug: String(id), birth_year: null, active, auto_save_enabled: false,
  threshold_offset: 0, group_ids: [], reference_count: 0, today: 0, last_7_days: 0, created_at: "2026-10-01T00:00:00",
});
const detail = { id: 1, size: 3, label_status: "unlabeled", kid_id: null, faces: [face(11), face(12), face(13)], next_cursor: null };

beforeEach(() => {
  document.cookie = "kino_csrf=tok; path=/";
  calls = [];
  routes = {
    "GET /api/kids": () => json(200, [kid(1, "Noa"), kid(2, "Itai"), kid(3, "Old", false)]),
    "GET /api/clusters": () => json(200, { items: [card(1, 3), card(2)], next_cursor: null }),
    "GET /api/clusters/1": () => json(200, detail),
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const method = (init.method ?? "GET").toUpperCase();
      const call = { method, url, body: init.body ? JSON.parse(init.body as string) : undefined };
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
            <ClustersPage />
          </MemoryRouter>
          <Toaster />
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}
const writes = () => calls.filter((c) => c.method !== "GET");

async function openFirst() {
  const user = userEvent.setup();
  renderPage();
  await user.click(await screen.findByTestId("cluster-1"));
  await screen.findByTestId("face-11");
  return user;
}

describe("ClustersPage", () => {
  it("shows cards with 6 crops, count, group and dates, and loads more", async () => {
    let n = 0;
    routes["GET /api/clusters"] = (c) => {
      n++;
      return c.url.includes("cursor=c2")
        ? json(200, { items: [card(3, 4)], next_cursor: null })
        : json(200, { items: [card(1, 3)], next_cursor: "c2" });
    };
    const user = userEvent.setup();
    renderPage();
    const c1 = await screen.findByTestId("cluster-1");
    expect(c1.querySelectorAll("img")).toHaveLength(6);
    expect(within(c1).getByText("3 faces")).toBeInTheDocument();
    expect(within(c1).getByText("Class 3B")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByTestId("cluster-3")).toBeInTheDocument();
    expect(n).toBe(2);
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });

  it("deselects crops and labels with the remaining face ids", async () => {
    routes["POST /api/clusters/1/label"] = () =>
      json(200, { cluster_id: 1, kid_id: 1, selected: 2, references_created: 2, duplicates: 0,
        rescore: { examined: 50, created: 1, updated: 0, unchanged: 0, protected: 0, saved: 0, retired: 0 } });
    const user = await openFirst();
    expect(screen.getByTestId("selected-count")).toHaveTextContent("3 of 3 selected");
    await user.click(screen.getByTestId("face-12"));
    expect(screen.getByTestId("selected-count")).toHaveTextContent("2 of 3 selected");
    expect(screen.getByTestId("face-12")).toHaveAttribute("aria-pressed", "false");
    const select = screen.getByLabelText("Kid");
    expect(within(select).queryByText("Old")).not.toBeInTheDocument();
    await user.selectOptions(select, "Noa");
    await user.click(screen.getByRole("button", { name: "This is Noa" }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]).toMatchObject({ url: "/api/clusters/1/label", body: { kid_id: 1, face_ids: [11, 13] } });
    expect(await screen.findByText("2 faces added as references for Noa")).toBeInTheDocument();
    expect(screen.getByText(/Re-scoring ran/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId("face-11")).not.toBeInTheDocument());
  });

  it("asks for confirmation before ignoring", async () => {
    routes["POST /api/clusters/1/ignore"] = () => json(200, { faces_deleted: 3, files_removed: 3 });
    const user = await openFirst();
    await user.click(screen.getByRole("button", { name: /Not my kid/ }));
    expect(await screen.findByText(/deleted immediately/)).toHaveTextContent(/cannot be undone/);
    expect(writes()).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(writes()).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: /Not my kid/ }));
    await user.click(await screen.findByRole("button", { name: "Delete permanently" }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    expect(writes()[0]!.url).toBe("/api/clusters/1/ignore");
    expect(await screen.findByText("Cluster ignored")).toBeInTheDocument();
  });

  it("shows a friendly message when a split returns 409", async () => {
    routes["POST /api/clusters/1/split"] = () => json(409, { detail: "cannot split further" });
    const user = await openFirst();
    await user.click(screen.getByRole("button", { name: "Split" }));
    expect(await screen.findByText("This cluster cannot be split any further")).toBeInTheDocument();
    expect(screen.getByTestId("face-11")).toBeInTheDocument(); // dialog stays open
  });

  it("refreshes the list when the opened cluster is gone (404)", async () => {
    routes["POST /api/clusters/1/split"] = () => json(404, { detail: "cluster 1 not found" });
    const user = await openFirst();
    const before = calls.filter((c) => c.url === "/api/clusters" || c.url.startsWith("/api/clusters?")).length;
    await user.click(screen.getByRole("button", { name: "Split" }));
    expect(await screen.findByText("This cluster changed; the list was refreshed")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId("face-11")).not.toBeInTheDocument());
    await waitFor(() =>
      expect(calls.filter((c) => c.url.startsWith("/api/clusters?")).length).toBeGreaterThan(before),
    );
  });

  it("handles a 404 when loading the cluster detail", async () => {
    routes["GET /api/clusters/1"] = () => json(404, { detail: "cluster 1 not found" });
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByTestId("cluster-1"));
    expect(await screen.findByText("This cluster changed; the list was refreshed")).toBeInTheDocument();
  });

  it("runs clustering now", async () => {
    routes["POST /api/clusters/run"] = () => json(202, { status: "queued", job_id: "j1" });
    const user = userEvent.setup();
    renderPage();
    await screen.findByTestId("cluster-1");
    await user.click(screen.getByRole("button", { name: "Find clusters now" }));
    await waitFor(() => expect(writes().map((c) => c.url)).toEqual(["/api/clusters/run"]));
    expect(await screen.findByText("Looking for clusters")).toBeInTheDocument();
  });

  it("shows the empty state with a disabled import button and kids hint", async () => {
    routes["GET /api/clusters"] = () => json(200, { items: [], next_cursor: null });
    routes["GET /api/kids"] = () => json(200, []);
    renderPage();
    expect(await screen.findByText(/Import the last 30 days from your groups, then label the clusters/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Import last 30 days" })).toBeDisabled();
    expect(await screen.findByRole("link", { name: "Add a kid first" })).toHaveAttribute("href", "/kids");
    expect(writes()).toHaveLength(0);
  });

  it("shows an error with retry", async () => {
    let fail = true;
    routes["GET /api/clusters"] = () => (fail ? json(500, { detail: "boom" }) : json(200, { items: [card(1)], next_cursor: null }));
    const user = userEvent.setup();
    renderPage();
    expect(await screen.findByText("Could not load clusters", {}, { timeout: 6000 })).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByTestId("cluster-1")).toBeInTheDocument();
  }, 15000);
});
