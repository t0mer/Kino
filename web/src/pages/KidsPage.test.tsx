import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeProvider } from "@/components/theme-provider";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { Kid } from "@/lib/kids";
import { createQueryClient } from "@/lib/query";
import { KidsPage } from "./KidsPage";

type Call = { method: string; url: string; body: unknown };
type Handler = (call: Call) => Response;
let routes: Record<string, Handler>;
let calls: Call[];

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const noa: Kid = {
  id: 1,
  display_name: "נועה",
  slug: "noa",
  birth_year: 2018,
  active: true,
  auto_save_enabled: false,
  threshold_offset: 0,
  group_ids: [10],
  reference_count: 4,
  today: 2,
  last_7_days: 9,
  created_at: "2026-10-01T10:00:00",
};
const itai: Kid = { ...noa, id: 2, display_name: "Itai", slug: "itai", reference_count: 0, today: 0, last_7_days: 1, group_ids: [] };

const coverage = (kid_id: number, count: number, hints: string[]) => ({
  kid_id,
  count,
  recommended: 10,
  yaw_known: count,
  yaw_min: null,
  yaw_max: null,
  left: 0,
  frontal: count,
  right: 0,
  dated: count,
  older_than_year: 0,
  oldest: null,
  newest: null,
  hints,
});

beforeEach(() => {
  document.cookie = "kino_csrf=tok; path=/";
  calls = [];
  routes = {
    "GET /api/kids": () => json(200, [noa, itai]),
    "GET /api/kids/1/coverage": () =>
      json(200, coverage(1, 4, ["Add 6 more references (recommended minimum is 10)", "Add side angles"])),
    "GET /api/kids/2/coverage": () => json(200, coverage(2, 0, ["Add 10 more references (recommended minimum is 10)"])),
    "GET /api/kids/1/references": () =>
      json(200, [
        { id: 5, kid_id: 1, source: "upload", status: "active", quality: 0.4, photo_date: null, crop_url: "/api/references/5/crop" },
        { id: 6, kid_id: 1, source: "upload", status: "active", quality: 0.9, photo_date: null, crop_url: "/api/references/6/crop" },
      ]),
    "GET /api/groups": () =>
      json(200, [
        { id: 10, chat_id: "10@g.us", name: "כיתה ב", watched: true, kid_ids: [1], is_forward_destination: false },
        { id: 11, chat_id: "11@g.us", name: "Football", watched: false, kid_ids: [], is_forward_destination: false },
      ]),
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const method = (init.method ?? "GET").toUpperCase();
      const path = url.split("?")[0]!;
      const call = { method, url, body: init.body ? JSON.parse(String(init.body)) : undefined };
      calls.push(call);
      const h = routes[`${method} ${path}`];
      return h ? h(call) : json(404, { detail: "not found" });
    }),
  );
  if (!("ResizeObserver" in window)) {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
  }
});
afterEach(() => vi.unstubAllGlobals());

function renderPage() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <ThemeProvider>
        <TooltipProvider>
          <MemoryRouter initialEntries={["/kids"]}>
            <Routes>
              <Route path="/kids" element={<KidsPage />} />
              <Route path="/kids/:id/enroll" element={<p>enroll page</p>} />
            </Routes>
          </MemoryRouter>
          <Toaster />
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}

const sent = (method: string, path: string) => calls.filter((c) => c.method === method && c.url.split("?")[0] === path);

describe("KidsPage", () => {
  it("shows kid cards with counts, coverage hints and avatar", async () => {
    renderPage();
    const card = await screen.findByTestId("kid-card-1");
    const heading = within(card).getByRole("heading", { name: "נועה" });
    expect(heading).toHaveAttribute("dir", "auto");
    expect(within(card).getByText("9")).toBeInTheDocument(); // 7 days
    expect(await within(card).findByText("Add side angles")).toBeInTheDocument();
    expect(within(card).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "4");
    expect(await within(card).findByRole("img")).toHaveAttribute("src", "/api/references/6/crop");
    // A kid with no references gets initials and no reference request.
    const card2 = screen.getByTestId("kid-card-2");
    expect(within(card2).getByText("I")).toBeInTheDocument();
    expect(sent("GET", "/api/kids/2/references")).toHaveLength(0);
    expect(within(card2).getByRole("link", { name: /Enroll faces/ })).toHaveAttribute("href", "/kids/2/enroll");
  });

  it("shows an empty state", async () => {
    routes["GET /api/kids"] = () => json(200, []);
    renderPage();
    expect(await screen.findByText("No kids yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Add kid/ })).toBeInTheDocument();
  });

  it("shows an error with retry", async () => {
    let fail = true;
    routes["GET /api/kids"] = () => (fail ? json(500, { detail: "db locked" }) : json(200, [itai]));
    renderPage();
    expect(await screen.findByText("Could not load kids", {}, { timeout: 5000 })).toBeInTheDocument();
    fail = false;
    await userEvent.setup().click(screen.getByRole("button", { name: /Retry/ }));
    expect(await screen.findByTestId("kid-card-2")).toBeInTheDocument();
  }, 10_000);

  it("creates a kid, showing a slug conflict inline first", async () => {
    let attempt = 0;
    routes["POST /api/kids"] = (c) => {
      attempt += 1;
      if (attempt === 1) return json(409, { detail: "slug 'itai' is already used by another kid" });
      const b = c.body as { display_name: string; slug: string };
      return json(201, { ...itai, id: 3, display_name: b.display_name, slug: b.slug });
    };
    renderPage();
    const user = userEvent.setup();
    await screen.findByTestId("kid-card-1");
    await user.click(screen.getByRole("button", { name: /Add kid/ }));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("Display name"), "Itai");
    await user.type(within(dialog).getByLabelText("Folder name (slug)"), "itai");
    await user.type(within(dialog).getByLabelText("Birth year"), "2020");
    await user.click(await within(dialog).findByRole("checkbox", { name: /Football/ }));
    await user.click(within(dialog).getByRole("button", { name: "Add kid" }));
    expect(await within(dialog).findByText("slug 'itai' is already used by another kid")).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Folder name (slug)")).toHaveAttribute("aria-invalid", "true");
    expect(sent("POST", "/api/kids")[0]!.body).toEqual({
      display_name: "Itai",
      slug: "itai",
      birth_year: 2020,
      active: true,
      threshold_offset: 0,
      group_ids: [11],
    });

    const slug = within(dialog).getByLabelText("Folder name (slug)");
    await user.clear(slug);
    await user.type(slug, "itai-2");
    await user.click(within(dialog).getByRole("button", { name: "Add kid" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(sent("POST", "/api/kids")[1]!.body).toMatchObject({ slug: "itai-2" });
    expect(await screen.findByText("Added Itai")).toBeInTheDocument();
  });

  it("validates the name before sending", async () => {
    renderPage();
    const user = userEvent.setup();
    await screen.findByTestId("kid-card-1");
    await user.click(screen.getByRole("button", { name: /Add kid/ }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Add kid" }));
    expect(within(dialog).getByText("Enter a name.")).toBeInTheDocument();
    expect(sent("POST", "/api/kids")).toHaveLength(0);
  });

  it("edits a kid, sending only changed fields", async () => {
    routes["PATCH /api/kids/1"] = () => json(200, { ...noa, birth_year: null, group_ids: [] });
    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Edit נועה" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByLabelText("Display name")).toHaveValue("נועה");
    await user.clear(within(dialog).getByLabelText("Birth year"));
    await user.click(await within(dialog).findByRole("checkbox", { name: /כיתה ב/ }));
    await user.click(within(dialog).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(sent("PATCH", "/api/kids/1")[0]!.body).toEqual({ birth_year: null, group_ids: [] });
  });

  it("toggles auto-save", async () => {
    routes["PATCH /api/kids/1"] = () => json(200, { ...noa, auto_save_enabled: true });
    renderPage();
    const user = userEvent.setup();
    const card = await screen.findByTestId("kid-card-1");
    const sw = within(card).getByRole("switch", { name: "Auto-save" });
    await user.click(sw);
    await waitFor(() => expect(sent("PATCH", "/api/kids/1")).toHaveLength(1));
    expect(sent("PATCH", "/api/kids/1")[0]!.body).toEqual({ auto_save_enabled: true });
    expect(await screen.findByText("Auto-save on for נועה")).toBeInTheDocument();
  });

  it("rolls back auto-save on failure", async () => {
    routes["PATCH /api/kids/1"] = () => json(500, { detail: "boom" });
    renderPage();
    const user = userEvent.setup();
    const card = await screen.findByTestId("kid-card-1");
    await user.click(within(card).getByRole("switch", { name: "Auto-save" }));
    expect(await screen.findByText("Could not change auto-save for נועה")).toBeInTheDocument();
    await waitFor(() => expect(within(card).getByRole("switch", { name: "Auto-save" })).not.toBeChecked());
  });

  it("deletes a kid after confirmation", async () => {
    routes["DELETE /api/kids/2"] = () => json(200, { id: 2, removed_files: 3, cancelled_forwards: 1 });
    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Delete Itai" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Pending forwards that only name this kid are cancelled/)).toBeInTheDocument();
    expect(sent("DELETE", "/api/kids/2")).toHaveLength(0);
    routes["GET /api/kids"] = () => json(200, [noa]);
    await user.click(within(dialog).getByRole("button", { name: "Delete kid" }));
    expect(await screen.findByText("Deleted Itai")).toBeInTheDocument();
    expect(screen.getByText("3 photo files removed, 1 forward cancelled.")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId("kid-card-2")).not.toBeInTheDocument());
  });
});
