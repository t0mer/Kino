import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeProvider } from "@/components/theme-provider";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { ActivityImage, Forward, ImageDetail } from "@/lib/activity";
import { createQueryClient } from "@/lib/query";
import { ActivityPage } from "./ActivityPage";

type Call = { method: string; url: string };
let routes: Record<string, (c: Call) => Response>;
let calls: Call[];
const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const image = (id: number, over: Partial<ActivityImage> = {}): ActivityImage => ({
  id, group_id: 10, group_name: "כיתה ב", sender: null, sent_at: null, received_at: "2026-10-11T08:00:00",
  status: "done", skip_reason: null, processing_ms: 850, error: null, face_count: 2, matches: { auto_saved: 1 }, has_file: true, ...over,
});
const fwd = (id: number, over: Partial<Forward> = {}): Forward => ({
  id, image_id: 5, dest_chat_id: "x@g.us", kid_ids: [1], caption: null, status: "failed", attempts: 4,
  last_error: "send failed <b>x</b>", created_at: "2026-10-11T08:00:00", sent_at: null, next_attempt_at: null, ...over,
});
const detail: ImageDetail = {
  id: 1, group_id: 10, group_name: "כיתה ב", sender: "Dana", sent_at: null, received_at: "2026-10-11T08:00:00",
  width: 1000, height: 500, status: "done", skip_reason: null, duplicate_of: null, processing_ms: 850,
  error: "boom <script>", has_file: true, file_url: "/api/images/1/file",
  faces: [
    { id: 7, bbox: [100, 50, 200, 100], det_score: 0.9, size_px: 100, quality: 0.8, tier: "L", from_tile: false, crop_url: "/api/crops/7" },
    { id: 8, bbox: [500, 250, 50, 50], det_score: 0.7, size_px: 30, quality: null, tier: "S", from_tile: true, crop_url: null },
  ],
  matches: [{ id: 3, face_id: 7, kid_id: 1, kid_name: "Noa", score: 0.61, margin: 0.2, method: "face", decision: "auto_saved", decided_by: "system" }],
  forwards: [{ id: 9, channel: "telegram", status: "sent", attempts: 1, kid_ids: [1], last_error: null, created_at: "2026-10-11T08:01:00", sent_at: "2026-10-11T08:02:00" }],
};
const stats = {
  images: { queued: 1, processing: 0, done: 12, failed: 0, skipped: 3 }, queue_depth: 1, review_count: 4,
  forwards: { pending: 1, sent: 5, failed: 1, cancelled: 0 }, forward_queue_depth: 1, kids: [],
};

beforeEach(() => {
  document.cookie = "kino_csrf=tok; path=/";
  calls = [];
  routes = {
    "GET /api/groups": () => json(200, [{ id: 10, chat_id: "10@g.us", name: "כיתה ב", slug: null, watched: true, kid_ids: [], is_forward_destination: false, created_at: "2026-10-01T00:00:00", last_seen_at: null }]),
    "GET /api/stats": () => json(200, stats),
    "GET /api/images": () => json(200, { items: [image(1), image(2, { status: "skipped", skip_reason: "duplicate", has_file: false, matches: {} })], next_cursor: null }),
    "GET /api/images/1": () => json(200, detail),
    "GET /api/forwards": (c) => json(200, c.url.includes("status=failed") ? [fwd(11)] : []),
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

function renderPage() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <ThemeProvider>
        <TooltipProvider>
          <ActivityPage />
          <Toaster />
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}
const imageGets = () => calls.filter((c) => c.method === "GET" && c.url.startsWith("/api/images?"));

describe("ActivityPage", () => {
  it("lists images with status, summary and stats", async () => {
    renderPage();
    const row = await screen.findByRole("button", { name: "Open image 1" });
    expect(within(row).getByText("כיתה ב")).toHaveAttribute("dir", "auto");
    expect(within(row).getByText(/2 faces · 1 auto saved · 850 ms/)).toBeInTheDocument();
    expect(within(screen.getByRole("button", { name: "Open image 2" })).getByText("skipped (duplicate)")).toBeInTheDocument();
    expect(await screen.findByText("Review pending")).toBeInTheDocument();
    expect(await screen.findByLabelText("1 failed forwards")).toBeInTheDocument();
  });

  it("sends filters to the API", async () => {
    renderPage();
    await screen.findByRole("button", { name: "Open image 1" });
    await userEvent.selectOptions(screen.getByLabelText("Status"), "failed");
    await userEvent.selectOptions(screen.getByLabelText("Group"), "10");
    await waitFor(() => {
      const last = imageGets().at(-1)!.url;
      expect(last).toContain("status=failed");
      expect(last).toContain("group_id=10");
    });
  });

  it("loads more with the cursor", async () => {
    routes["GET /api/images"] = (c) =>
      c.url.includes("cursor=1")
        ? json(200, { items: [image(1)], next_cursor: null })
        : json(200, { items: [image(3)], next_cursor: 1 });
    renderPage();
    await screen.findByRole("button", { name: "Open image 3" });
    await userEvent.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByRole("button", { name: "Open image 1" })).toBeInTheDocument();
    expect(imageGets().at(-1)!.url).toContain("cursor=1");
  });

  it("shows the detail with face boxes, matches, forwards and error text", async () => {
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Open image 1" }));
    const img = await screen.findByAltText("Photo with detected faces");
    Object.defineProperty(img, "naturalWidth", { value: 1000 });
    Object.defineProperty(img, "naturalHeight", { value: 500 });
    img.dispatchEvent(new Event("load"));
    const boxes = await screen.findAllByTestId("face-box");
    expect(boxes).toHaveLength(2);
    expect(boxes[0]).toHaveStyle({ left: "10%", top: "10%", width: "20%", height: "20%" });
    const dlg = screen.getByRole("dialog");
    for (const n of within(dlg).getAllByText("Noa")) expect(n).toHaveAttribute("dir", "auto");
    expect(within(dlg).getByText("tier L")).toBeInTheDocument();
    expect(within(dlg).getByText("no match")).toBeInTheDocument();
    expect(within(dlg).getByText("sent")).toBeInTheDocument();
    expect(within(dlg).getByTestId("forward-channel")).toHaveTextContent("Telegram");
    expect(within(dlg).getByRole("alert")).toHaveTextContent("boom <script>");
  });

  it("shows a channel badge on failed and pending forwards", async () => {
    routes["GET /api/forwards"] = (c) =>
      json(200, c.url.includes("status=failed") ? [fwd(11, { channel: "telegram" })] : [fwd(12, { channel: "whatsapp", status: "pending", last_error: null })]);
    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: /Forwards/ }));
    const failed = within(await screen.findByRole("region", { name: "Failed forwards" }));
    expect(failed.getByTestId("forward-channel")).toHaveTextContent("Telegram");
    const pending = within(await screen.findByRole("region", { name: "Pending forwards" }));
    await waitFor(() => expect(pending.getByTestId("forward-channel")).toHaveTextContent("WhatsApp"));
  });

  it("retries a failed forward", async () => {
    let retried = false;
    routes["POST /api/forwards/11/retry"] = () => {
      retried = true;
      return json(200, fwd(11, { status: "pending" }));
    };
    routes["GET /api/forwards"] = (c) =>
      json(200, c.url.includes("status=failed") && !retried ? [fwd(11)] : []);
    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: /Forwards/ }));
    expect(await screen.findByText("send failed <b>x</b>")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Retry forward 11" }));
    expect(await screen.findByText("Forward queued for retry")).toBeInTheDocument();
    expect(await screen.findByText("No failed forwards.")).toBeInTheDocument();
  });

  it("shows a toast when the retry fails", async () => {
    routes["POST /api/forwards/11/retry"] = () => json(409, { detail: "not failed" });
    renderPage();
    await userEvent.click(await screen.findByRole("tab", { name: /Forwards/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Retry forward 11" }));
    expect(await screen.findByText("Could not retry the forward")).toBeInTheDocument();
  });

  it("shows the empty and error states", async () => {
    routes["GET /api/images"] = () => json(200, { items: [], next_cursor: null });
    const { unmount } = renderPage();
    expect(await screen.findByText("No images yet")).toBeInTheDocument();
    unmount();
    routes["GET /api/images"] = () => json(500, { detail: "db down" });
    renderPage();
    expect(await screen.findByText("Could not load activity", undefined, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Retry/ })).toBeInTheDocument();
  });
});
