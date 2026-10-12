import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeProvider } from "@/components/theme-provider";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { GalleryRef } from "@/lib/gallery";
import { createQueryClient } from "@/lib/query";
import { GalleryPage } from "./GalleryPage";

type Call = { method: string; url: string; body: unknown };
let routes: Record<string, (c: Call) => Response>;
let calls: Call[];
const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const ref = (id: number, over: Partial<GalleryRef> = {}): GalleryRef => ({
  id, kid_id: 1, face_id: id, source: "upload", status: "active", quality: 0.8, photo_date: "2026-09-10T00:00:00",
  added_at: "2026-10-01T00:00:00", weight: 0.9, yaw: 0, crop_url: `/api/references/${id}/crop`, ...over,
});
const kid = (id: number, display_name: string) => ({
  id, display_name, slug: String(id), birth_year: null, active: true, auto_save_enabled: false,
  threshold_offset: 0, group_ids: [], reference_count: 0, today: 0, last_7_days: 0, created_at: "2026-10-01T00:00:00",
});
const cov = (kid_id: number) => ({
  kid_id, count: 3, recommended: 10, yaw_known: 0, left: 0, frontal: 0, right: 0, older_than_year: 0, hints: ["Add side angles"],
});

let refs: GalleryRef[];

beforeEach(() => {
  document.cookie = "kino_csrf=tok; path=/";
  calls = [];
  refs = [ref(1, { photo_date: "2026-08-10T00:00:00" }), ref(2, { source: "auto", photo_date: "2026-09-10T00:00:00" }), ref(3, { status: "retired", photo_date: null })];
  routes = {
    "GET /api/kids": () => json(200, [kid(1, "Noa"), kid(2, "Itai")]),
    "GET /api/kids/1/coverage": () => json(200, cov(1)),
    "GET /api/kids/1/references": () => json(200, refs),
    "GET /api/kids/2/references": () => json(200, []),
    "GET /api/kids/2/coverage": () => json(200, cov(2)),
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

function renderPage(path = "/gallery") {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <ThemeProvider>
        <TooltipProvider>
          <MemoryRouter initialEntries={[path]}>
            <GalleryPage />
          </MemoryRouter>
          <Toaster />
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}
const writes = () => calls.filter((c) => c.method !== "GET");

describe("GalleryPage", () => {
  it("shows references newest first with badges, crop and histogram", async () => {
    renderPage();
    const cards = await screen.findAllByTestId(/^ref-/);
    expect(cards.map((c) => c.dataset.testid)).toEqual(["ref-2", "ref-1", "ref-3"]);
    expect(within(cards[0]!).getByText("auto")).toBeInTheDocument();
    expect(within(cards[2]!).getByText("retired")).toBeInTheDocument();
    expect(cards[0]!.querySelector("img")).toHaveAttribute("src", "/api/references/2/crop");
    expect(screen.getAllByTestId("hist-bar")).toHaveLength(2);
    expect(screen.getByText("Add side angles")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Add references/ })).toHaveAttribute("href", "/kids/1/enroll");
  });

  it("flags outliers first with a reason tooltip, a banner and an Outliers-only toggle", async () => {
    refs = [
      ref(1, { photo_date: "2026-08-10T00:00:00" }),
      ref(2, { photo_date: "2026-09-10T00:00:00" }),
      ref(3, { photo_date: "2026-01-10T00:00:00", outlier: true, outlier_reason: "closer_to_other_kid" }),
    ];
    renderPage();
    const cards = await screen.findAllByTestId(/^ref-/);
    expect(cards.map((c) => c.dataset.testid)).toEqual(["ref-3", "ref-2", "ref-1"]);
    expect(screen.getByRole("status")).toHaveTextContent("1 reference looks different from the rest; review or retire it.");
    expect(within(cards[0]!).getByText("Outlier")).toBeInTheDocument();
    await userEvent.hover(within(cards[0]!).getByText("Outlier"));
    expect((await screen.findAllByText(/more like another kid/)).length).toBeGreaterThan(0);

    const toggle = screen.getByRole("button", { name: "Outliers only (1)" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(toggle);
    expect(screen.getAllByTestId(/^ref-/).map((c) => c.dataset.testid)).toEqual(["ref-3"]);
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(toggle);
    expect(screen.getAllByTestId(/^ref-/)).toHaveLength(3);
  });

  it("shows no outlier UI when nothing is flagged", async () => {
    renderPage();
    await screen.findAllByTestId(/^ref-/);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByText("Outlier")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Outliers only/ })).not.toBeInTheDocument();
  });

  it("selects the kid from ?kid= and shows the empty state", async () => {
    renderPage("/gallery?kid=2");
    expect(await screen.findByText("No references yet")).toBeInTheDocument();
    expect((screen.getByLabelText("Kid") as HTMLSelectElement).value).toBe("2");
  });

  it("sends source/status to the API and filters dates client-side", async () => {
    renderPage();
    await screen.findAllByTestId(/^ref-/);
    await userEvent.selectOptions(screen.getByLabelText("Source"), "auto");
    await waitFor(() => expect(calls.at(-1)!.url).toContain("source=auto"));
    await userEvent.selectOptions(screen.getByLabelText("Source"), "");
    await userEvent.type(screen.getByLabelText("From"), "2026-09-01");
    await waitFor(() => expect(screen.getAllByTestId(/^ref-/)).toHaveLength(1));
    expect(screen.getByTestId("ref-2")).toBeInTheDocument();
  });

  it("retires and reactivates", async () => {
    routes["PATCH /api/references/1"] = () => json(200, ref(1, { status: "retired" }));
    routes["PATCH /api/references/3"] = () => json(200, ref(3));
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Retire reference 1" }));
    await waitFor(() => expect(writes()[0]).toMatchObject({ method: "PATCH", url: "/api/references/1", body: { status: "retired" } }));
    await userEvent.click(await screen.findByRole("button", { name: "Reactivate reference 3" }));
    await waitFor(() => expect(writes().at(-1)).toMatchObject({ url: "/api/references/3", body: { status: "active" } }));
  });

  it("confirms before deleting", async () => {
    routes["DELETE /api/references/1"] = () => new Response(null, { status: 204 });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Delete reference 1" }));
    expect(writes()).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(writes()).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Delete reference 1" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete reference" }));
    await waitFor(() => expect(writes()[0]).toMatchObject({ method: "DELETE", url: "/api/references/1" }));
  });

  it("confirms before marking as negative", async () => {
    routes["POST /api/references/2/to-negative"] = () => json(200, { id: 1, kid_id: 1, source_face_id: 2, added_at: "2026-10-01T00:00:00" });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Mark reference 2 as negative" }));
    expect(screen.getByText(/the reference is removed from the gallery/)).toBeInTheDocument();
    expect(writes()).toHaveLength(0);
    await userEvent.click(screen.getByRole("button", { name: "Mark as someone else" }));
    await waitFor(() => expect(writes()[0]).toMatchObject({ method: "POST", url: "/api/references/2/to-negative" }));
  });

  it("moves a reference to another kid", async () => {
    routes["PATCH /api/references/1"] = () => json(200, ref(1, { kid_id: 2 }));
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Move reference 1" }));
    const move = screen.getByRole("button", { name: "Move" });
    expect(move).toBeDisabled();
    await userEvent.selectOptions(screen.getByLabelText("Move to"), "2");
    await userEvent.click(move);
    await waitFor(() => expect(writes()[0]).toMatchObject({ url: "/api/references/1", body: { kid_id: 2 } }));
  });

  it("edits and clears the photo date", async () => {
    routes["PATCH /api/references/1"] = () => json(200, ref(1));
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Edit date of reference 1" }));
    const input = screen.getByLabelText("Date") as HTMLInputElement;
    expect(input.value).toBe("2026-08-10");
    await userEvent.clear(input);
    await userEvent.type(input, "2026-07-04");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(writes()[0]).toMatchObject({ body: { photo_date: "2026-07-04T00:00:00" } }));
    await userEvent.click(await screen.findByRole("button", { name: "Edit date of reference 1" }));
    await userEvent.click(screen.getByRole("button", { name: "Clear date" }));
    await waitFor(() => expect(writes().at(-1)).toMatchObject({ body: { photo_date: null } }));
  });

  it("shows an error toast when an action fails", async () => {
    routes["DELETE /api/references/1"] = () => json(404, { detail: "reference not found" });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Delete reference 1" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete reference" }));
    expect(await screen.findByText("Could not delete the reference")).toBeInTheDocument();
  });

  it("shows a retry state when loading fails", async () => {
    routes["GET /api/kids/1/references"] = () => json(500, { detail: "boom" });
    renderPage();
    expect(await screen.findByText("Could not load references", undefined, { timeout: 4000 })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Retry/ })).toBeInTheDocument();
  });
});
