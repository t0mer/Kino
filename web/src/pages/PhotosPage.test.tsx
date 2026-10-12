import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeProvider } from "@/components/theme-provider";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { Photo } from "@/lib/photos";
import { createQueryClient } from "@/lib/query";
import { PhotosPage } from "./PhotosPage";

type Call = { method: string; url: string };
let routes: Record<string, (c: Call) => Response>;
let calls: Call[];
const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const photo = (id: number, image_id: number, kid_id: number, kid_name: string): Photo => ({
  id, image_id, kid_id, kid_name, group_id: 10, group_name: "כיתה ב",
  sent_at: "2026-10-05T10:00:00", saved_at: "2026-10-05T10:01:00", file_url: `/api/images/${image_id}/file`,
});
const kid = (id: number, display_name: string) => ({
  id, display_name, slug: String(id), birth_year: null, active: true, auto_save_enabled: false,
  threshold_offset: 0, group_ids: [], reference_count: 0, today: 0, last_7_days: 0, created_at: "2026-10-01T00:00:00",
});

beforeEach(() => {
  document.cookie = "kino_csrf=tok; path=/";
  calls = [];
  routes = {
    "GET /api/kids": () => json(200, [kid(1, "Noa"), kid(2, "Itai")]),
    "GET /api/groups": () => json(200, [{ id: 10, chat_id: "10@g.us", name: "כיתה ב", slug: null, watched: true, kid_ids: [], is_forward_destination: false, created_at: "2026-10-01T00:00:00", last_seen_at: null }]),
    "GET /api/photos": () => json(200, { items: [photo(1, 100, 1, "Noa"), photo(2, 100, 2, "Itai")], next_cursor: null }),
  };
  vi.stubGlobal("IntersectionObserver", class { observe() {} disconnect() {} unobserve() {} });
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

function renderPage() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <ThemeProvider>
        <TooltipProvider>
          <PhotosPage />
          <Toaster />
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}
const photoGets = () => calls.filter((c) => c.method === "GET" && c.url.startsWith("/api/photos"));

describe("PhotosPage", () => {
  it("renders a thumbnail grid", async () => {
    renderPage();
    const btn = await screen.findByRole("button", { name: "Open photo of Noa from כיתה ב" });
    expect(within(btn).getByText("Noa")).toHaveAttribute("dir", "auto");
    expect(btn.querySelector("img")).toHaveAttribute("src", "/api/images/100/file");
  });

  it("sends filters to the API", async () => {
    renderPage();
    await screen.findByRole("button", { name: /Open photo of Noa/ });
    await userEvent.selectOptions(screen.getByLabelText("Kid"), "2");
    await userEvent.selectOptions(screen.getByLabelText("Group"), "10");
    await userEvent.type(screen.getByLabelText("From"), "2026-10-01");
    await userEvent.type(screen.getByLabelText("To"), "2026-10-07");
    await waitFor(() => {
      const last = photoGets().at(-1)!.url;
      expect(last).toContain("kid_id=2");
      expect(last).toContain("group_id=10");
      expect(last).toContain("from=2026-10-01T00%3A00%3A00");
      expect(last).toContain("to=2026-10-07T23%3A59%3A59");
    });
  });

  it("loads more with the cursor", async () => {
    routes["GET /api/photos"] = (c) =>
      c.url.includes("cursor=2")
        ? json(200, { items: [photo(3, 101, 1, "Noa")], next_cursor: null })
        : json(200, { items: [photo(1, 100, 1, "Noa"), photo(2, 100, 2, "Itai")], next_cursor: 2 });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Load more" }));
    await waitFor(() => expect(screen.getAllByRole("button", { name: /Open photo/ })).toHaveLength(3));
    expect(photoGets().at(-1)!.url).toContain("cursor=2");
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });

  it("shows empty and error states", async () => {
    routes["GET /api/photos"] = () => json(200, { items: [], next_cursor: null });
    const { unmount } = renderPage();
    expect(await screen.findByText("No saved photos yet")).toBeInTheDocument();
    unmount();
    routes["GET /api/photos"] = () => json(400, { detail: "bad" });
    renderPage();
    expect(await screen.findByText("Could not load photos")).toBeInTheDocument();
  });

  it("opens the lightbox with metadata and removes after confirm", async () => {
    let removed = false;
    routes["GET /api/photos"] = () =>
      json(200, { items: removed ? [photo(2, 100, 2, "Itai")] : [photo(1, 100, 1, "Noa"), photo(2, 100, 2, "Itai")], next_cursor: null });
    routes["DELETE /api/photos/1"] = () => {
      removed = true;
      return json(200, { id: 1, removed_files: 1, cancelled_forwards: 0, rejected_matches: 1, retired_references: 0 });
    };
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /Open photo of Noa/ }));
    const dlg = await screen.findByRole("dialog");
    expect(within(dlg).getByRole("img")).toHaveAttribute("src", "/api/images/100/file");
    expect(within(dlg).getByText("Noa, Itai", { selector: "dd span" })).toBeInTheDocument();
    await userEvent.click(within(dlg).getByRole("button", { name: /Remove from Noa/ }));
    // Confirm dialog first: nothing deleted yet.
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    const confirm = (await screen.findAllByRole("dialog")).at(-1)!;
    await userEvent.click(within(confirm).getByRole("button", { name: "Remove photo" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url === "/api/photos/1")).toBe(true));
    expect(await screen.findByText("Removed from Noa")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("button", { name: /Open photo of Noa/ })).not.toBeInTheDocument());
  });

  it("cancelling the confirm does not delete", async () => {
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: /Open photo of Noa/ }));
    await userEvent.click(await screen.findByRole("button", { name: /Remove from Noa/ }));
    const confirm = (await screen.findAllByRole("dialog")).at(-1)!;
    await userEvent.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  });
});
