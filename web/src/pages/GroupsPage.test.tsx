import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeProvider } from "@/components/theme-provider";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { GroupItem } from "@/lib/groups";
import { createQueryClient } from "@/lib/query";
import { GroupsPage } from "./GroupsPage";

type Call = { method: string; url: string; body: unknown };
let routes: Record<string, (c: Call) => Response>;
let calls: Call[];
const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const grp = (id: number, name: string | null, over: Partial<GroupItem> = {}): GroupItem => ({
  id,
  chat_id: `${id}@g.us`,
  name,
  slug: null,
  watched: false,
  kid_ids: [],
  is_forward_destination: false,
  created_at: "2026-10-01T00:00:00",
  last_seen_at: "2026-10-10T00:00:00",
  ...over,
});
const kid = (id: number, display_name: string) => ({
  id, display_name, slug: String(id), birth_year: null, active: true, auto_save_enabled: false,
  threshold_offset: 0, group_ids: [], reference_count: 0, today: 0, last_7_days: 0, created_at: "2026-10-01T00:00:00",
});

beforeEach(() => {
  document.cookie = "kino_csrf=tok; path=/";
  calls = [];
  routes = {
    "GET /api/groups": () => json(200, [grp(10, "כיתה ב", { watched: true, kid_ids: [1] }), grp(11, "Football"), grp(12, "Dest", { is_forward_destination: true })]),
    "GET /api/kids": () => json(200, [kid(1, "Noa"), kid(2, "Itai")]),
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
          <GroupsPage />
          <Toaster />
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}
const patches = () => calls.filter((c) => c.method === "PATCH");

describe("GroupsPage", () => {
  it("lists groups with dir=auto names, watched first, and a disabled backfill", async () => {
    renderPage();
    const row = await screen.findByTestId("group-10");
    expect(within(row).getByRole("heading", { name: "כיתה ב" })).toHaveAttribute("dir", "auto");
    expect(within(row).getByRole("switch", { name: "Watch כיתה ב" })).toBeChecked();
    expect(within(row).getByRole("button", { name: "Noa" })).toHaveAttribute("aria-pressed", "true");
    expect(within(row).getByRole("button", { name: "Itai" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: "Backfill" })).toBeDisabled();
    expect(screen.getByLabelText("Backfill arrives in a later update")).toBeInTheDocument();
    expect(calls.some((c) => c.url.includes("backfill"))).toBe(false);
  });

  it("toggles watched and shows feedback", async () => {
    routes["PATCH /api/groups/11"] = () => json(200, grp(11, "Football", { watched: true }));
    renderPage();
    const row = await screen.findByTestId("group-11");
    await userEvent.click(within(row).getByRole("switch", { name: "Watch Football" }));
    await waitFor(() => expect(patches()[0]?.body).toEqual({ watched: true }));
    expect(await screen.findByText("Now watching Football")).toBeInTheDocument();
    await waitFor(() => expect(within(screen.getByTestId("group-11")).getByRole("switch")).toBeChecked());
  });

  it("maps kids with the full new id list", async () => {
    routes["PATCH /api/groups/10"] = () => json(200, grp(10, "כיתה ב", { watched: true, kid_ids: [1, 2] }));
    renderPage();
    const row = await screen.findByTestId("group-10");
    await userEvent.click(within(row).getByRole("button", { name: "Itai" }));
    await waitFor(() => expect(patches()[0]?.body).toEqual({ kid_ids: [1, 2] }));
    await waitFor(() => expect(within(screen.getByTestId("group-10")).getByRole("button", { name: "Itai" })).toHaveAttribute("aria-pressed", "true"));
  });

  it("notes the forward destination and surfaces the backend refusal", async () => {
    routes["PATCH /api/groups/12"] = () => json(409, { detail: "This chat is the forward destination and cannot be watched" });
    renderPage();
    const row = await screen.findByTestId("group-12");
    expect(within(row).getByText(/forward destination, so it cannot be watched/)).toBeInTheDocument();
    await userEvent.click(within(row).getByRole("switch", { name: "Watch Dest" }));
    expect(await screen.findByText("This chat is the forward destination and cannot be watched")).toBeInTheDocument();
    await waitFor(() => expect(within(screen.getByTestId("group-12")).getByRole("switch")).not.toBeChecked());
  });

  it("shows the empty state and refreshes from OpenWA", async () => {
    let n = 0;
    routes["GET /api/groups"] = () => json(200, n++ === 0 ? [] : [grp(5, "New")]);
    routes["POST /api/groups/refresh"] = () => json(200, { imported: 1, total: 1 });
    renderPage();
    expect(await screen.findByText(/Post a photo in a WhatsApp group/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Refresh/ }));
    expect(await screen.findByText("Imported 1 new group (1 total)")).toBeInTheDocument();
    expect(await screen.findByTestId("group-5")).toBeInTheDocument();
  });

  it("shows an error with retry", async () => {
    routes["GET /api/groups"] = () => json(500, { detail: "boom" });
    renderPage();
    expect(await screen.findByText("Could not load groups", undefined, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Retry/ })).toBeInTheDocument();
  });
});
