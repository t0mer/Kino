import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeProvider } from "@/components/theme-provider";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { createQueryClient } from "@/lib/query";
import type { OpenWASetupStatus } from "@/lib/openwaSetup";
import type { Chat, SettingsOut } from "@/lib/settings";
import { SettingsPage } from "./SettingsPage";

type Call = { method: string; url: string; body: unknown };
let routes: Record<string, (c: Call) => Response>;
let calls: Call[];
const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const chat = (id: string, name: string | null, over: Partial<Chat> = {}): Chat => ({
  id, name, is_group: id.endsWith("@g.us"), watched: false, selectable: true, ...over,
});
const kid = (id: number, display_name: string) => ({
  id, display_name, slug: String(id), birth_year: null, active: true, auto_save_enabled: false,
  threshold_offset: 0, group_ids: [], reference_count: 0, today: 0, last_7_days: 0, created_at: "2026-10-01T00:00:00",
});
const baseSettings = (): SettingsOut => ({
  forward: { enabled: false, default_chat_id: null, per_kid: {}, caption_template: "{kids} · {group}", include_review_confirmed: true, send_caption: true },
  digest: { chat_id: null, cron: "0 20 * * *", apprise_urls: ["tgram://****"], public_base_url: null },
  thresholds: { L: { auto: 0.45, review: 0.32 }, M: { auto: 0.5, review: 0.35 }, S: { review: 0.4 }, prior_bonus: 0.03, unmapped_penalty: 0.05, margin_min: 0.08, auto_floor: 0.3 },
  retention: { raw_days: 7, face_days: 30 },
  embedder: "auraface",
  models: [{ name: "scrfd_10g", filename: "det_10g.onnx", license: "MIT", sha256: "abc123", present: true }],
  version: "2026.10.0",
});
let settings: SettingsOut;
let setupStatus: OpenWASetupStatus;

beforeEach(() => {
  // Radix Slider measures its thumb with ResizeObserver, which jsdom lacks.
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  document.cookie = "kino_csrf=tok; path=/";
  calls = [];
  settings = baseSettings();
  setupStatus = {
    configured: true, url: "http://openwa:2785", session_id: "main", session_name: "Main phone",
    outgoing_session_id: null, outgoing_session_name: null, same_as_incoming: true,
    has_api_key: true, has_webhook_secret: true, webhook_secret_pending: false, webhook_url: "http://kino-api:8080/api/webhook/openwa",
    env_overrides: [],
  };
  routes = {
    "GET /api/settings": () => json(200, settings),
    "GET /api/openwa/setup/status": () => json(200, setupStatus),
    "GET /api/kids": () => json(200, [kid(1, "Noa"), kid(2, "איתי")]),
    "GET /api/openwa/chats": (c) => {
      const q = new URL(c.url, "http://x").searchParams.get("q");
      const all = [chat("111@g.us", "Family", {}), chat("222@g.us", "Class 3B", { watched: true, selectable: false }), chat("333@c.us", "Dana")];
      return json(200, q ? all.filter((x) => x.name?.toLowerCase().includes(q.toLowerCase())) : all);
    },
    "GET /api/openwa/webhook": () => json(200, { registered: true, active: true, id: "w1", url: "http://kino-api:8080/api/webhook/openwa", events: ["message.received"] }),
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
            <SettingsPage />
          </MemoryRouter>
          <Toaster />
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}
const puts = (path: string) => calls.filter((c) => c.method === "PUT" && c.url === path);
const section = async (id: string) => within(await screen.findByTestId(`section-${id}`));

async function pickChat(user: ReturnType<typeof userEvent.setup>, buttonName: RegExp, chatName: string) {
  await user.click(await screen.findByRole("button", { name: buttonName }));
  await user.click(await screen.findByRole("button", { name: new RegExp(chatName) }));
}

describe("SettingsPage", () => {
  it("shows an error with retry when settings fail to load", async () => {
    routes["GET /api/settings"] = () => json(403, { detail: "boom" });
    renderPage();
    expect(await screen.findByText("Could not load settings")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Retry/ })).toBeInTheDocument();
  });

  it("renders the Telegram section when settings include it", async () => {
    settings.telegram = {
      has_token: false, bot_username: null, api_url: "https://api.telegram.org", rate_per_min: 20,
      send_as_file: false, delete_on_remove: true, env_overrides: [],
    };
    renderPage();
    expect(await screen.findByTestId("section-telegram")).toBeInTheDocument();
  });

  it("picker searches (debounced) and disables watched groups with the reason", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: /Default destination: Not set/ }));
    const watched = await screen.findByRole("button", { name: /Class 3B/ });
    expect(watched).toBeDisabled();
    expect(within(watched).getByText("watched group - would loop")).toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: "Search chats" }), "dan");
    await waitFor(() => expect(calls.some((c) => c.url.includes("q=dan"))).toBe(true));
    expect(calls.filter((c) => c.url.includes("q=d")).length).toBe(1); // debounced: not one call per key
    await user.click(await screen.findByRole("button", { name: /Dana/ }));
    expect(await screen.findByRole("button", { name: /Default destination: Dana/ })).toBeInTheDocument();
  });

  it("saves the forwarding section with per-kid overrides, and clears an override", async () => {
    const user = userEvent.setup();
    routes["PUT /api/settings/forward"] = (c) => json(200, c.body);
    renderPage();
    const fwd = await section("forwarding");
    expect(fwd.getByRole("button", { name: "Save Forwarding" })).toBeDisabled();
    await user.click(fwd.getByRole("switch", { name: "Enable forwarding" }));
    await pickChat(user, /Default destination: Not set/, "Family");
    await pickChat(user, /Destination for איתי: Use default/, "Dana");
    expect(fwd.getByRole("button", { name: "Save Forwarding" })).toBeEnabled();
    expect(fwd.getByText("Unsaved changes")).toBeInTheDocument();
    await user.click(fwd.getByRole("button", { name: "Save Forwarding" }));
    await waitFor(() => expect(puts("/api/settings/forward")).toHaveLength(1));
    expect(puts("/api/settings/forward")[0]?.body).toMatchObject({ enabled: true, default_chat_id: "111@g.us", per_kid: { "2": "333@c.us" } });
    await waitFor(() => expect(fwd.queryByText("Unsaved changes")).not.toBeInTheDocument());
    await user.click(fwd.getByRole("button", { name: "Clear Destination for איתי" }));
    expect(fwd.getByRole("button", { name: /Destination for איתי: Use default/ })).toBeInTheDocument();
    expect(fwd.getByText("איתי")).toHaveAttribute("dir", "auto");
  });

  it("renders the caption preview live and warns about unknown placeholders", async () => {
    const user = userEvent.setup();
    renderPage();
    const fwd = await section("forwarding");
    const box = fwd.getByRole("textbox", { name: "Caption template" });
    await user.clear(box);
    await user.type(box, "{{kids} on {{date} {{oops}");
    expect(fwd.getByTestId("caption-preview")).toHaveTextContent("Noa, איתי on 2026-10-12 {oops}");
    expect(fwd.getByTestId("unknown-placeholders")).toHaveTextContent("{oops}");
    await user.clear(box);
    expect(fwd.getByText("The caption cannot be empty")).toBeInTheDocument();
    expect(fwd.getByRole("button", { name: "Save Forwarding" })).toBeDisabled();
  });

  it("maps a 422 from the API onto the field", async () => {
    const user = userEvent.setup();
    routes["PUT /api/settings/forward"] = () =>
      json(422, { detail: [{ loc: ["body", "per_kid", "2"], msg: "is a watched group" }] });
    renderPage();
    const fwd = await section("forwarding");
    await pickChat(user, /Destination for איתי: Use default/, "Dana");
    await user.click(fwd.getByRole("button", { name: "Save Forwarding" }));
    expect(await fwd.findByText("is a watched group")).toBeInTheDocument();
    expect(fwd.getByRole("button", { name: /^Destination for איתי: .*Change$/ })).toHaveAttribute("aria-invalid", "true");
    expect(await screen.findByText("Could not save forwarding settings")).toBeInTheDocument();
  });

  it("asks for confirmation before sending a real test photo", async () => {
    const user = userEvent.setup();
    routes["POST /api/forward/test"] = () => json(200, { sent: true });
    settings.forward.default_chat_id = "333@c.us";
    renderPage();
    const fwd = await section("forwarding");
    await user.click(fwd.getByRole("button", { name: /Send test photo/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/real WhatsApp message/)).toBeInTheDocument();
    expect(calls.some((c) => c.url === "/api/forward/test")).toBe(false);
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(calls.some((c) => c.url === "/api/forward/test")).toBe(false);
    await user.click(fwd.getByRole("button", { name: /Send test photo/ }));
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Send test photo" }));
    await waitFor(() => expect(calls.find((c) => c.url === "/api/forward/test")?.body).toEqual({ chat_id: "333@c.us" }));
    expect(await screen.findByText("Test photo sent")).toBeInTheDocument();
  });

  it("disables the test button without a destination", async () => {
    renderPage();
    const fwd = await section("forwarding");
    expect(fwd.getByRole("button", { name: /Send test photo/ })).toBeDisabled();
  });

  it("digest: shows the info note, masked URLs, and saves", async () => {
    const user = userEvent.setup();
    routes["PUT /api/settings/digest"] = (c) => json(200, { ...(c.body as object), public_base_url: "https://k.example.com" });
    renderPage();
    const d = await section("digest");
    expect(d.getByTestId("digest-note")).toHaveTextContent("A digest is sent at this time only when new photos are waiting for review.");
    expect(d.getByLabelText("Notification URL 1")).toHaveValue("tgram://****");
    await user.type(d.getByLabelText("Public base URL"), "https://k.example.com");
    await user.click(d.getByRole("button", { name: "Save Review digest" }));
    await waitFor(() => expect(puts("/api/settings/digest")).toHaveLength(1));
    expect(puts("/api/settings/digest")[0]?.body).toEqual({ chat_id: null, cron: "0 20 * * *", apprise_urls: ["tgram://****"], public_base_url: "https://k.example.com" });
  });

  it("digest: shows a cron error from the API", async () => {
    const user = userEvent.setup();
    routes["PUT /api/settings/digest"] = () => json(422, { detail: [{ loc: ["body", "cron"], msg: "Value error, cron needs 5 fields: minute hour day month weekday" }] });
    renderPage();
    const d = await section("digest");
    await user.type(d.getByLabelText("Check time (cron)"), " 1");
    await user.click(d.getByRole("button", { name: "Save Review digest" }));
    expect(await d.findByText(/cron needs 5 fields/)).toBeInTheDocument();
  });

  it("thresholds: blocks invalid values locally, maps server errors, and resets to defaults", async () => {
    const user = userEvent.setup();
    renderPage();
    const t = await section("thresholds");
    const save = t.getByRole("button", { name: "Save Thresholds" });
    const lAuto = t.getByRole("spinbutton", { name: /^L auto-save$/ });
    await user.clear(lAuto);
    await user.type(lAuto, "0.2");
    expect(t.getByText("Auto-save must be at least the review value")).toBeInTheDocument();
    expect(save).toBeDisabled();
    await user.clear(lAuto);
    await user.type(lAuto, "0.6");
    expect(save).toBeEnabled();

    routes["PUT /api/settings/thresholds"] = () => json(422, { detail: [{ loc: ["body", "M"], msg: "Value error, auto must be at least 0.30" }] });
    await user.click(save);
    expect(await t.findByText("auto must be at least 0.30")).toBeInTheDocument();

    await user.click(t.getByRole("button", { name: /Reset to defaults/ }));
    expect(lAuto).toHaveValue(0.45);
    expect(save).toBeDisabled(); // equals saved values again
  });

  it("thresholds: saves valid values", async () => {
    const user = userEvent.setup();
    routes["PUT /api/settings/thresholds"] = (c) => json(200, { ...(c.body as object), auto_floor: 0.3 });
    renderPage();
    const t = await section("thresholds");
    const sReview = t.getByRole("spinbutton", { name: /^S review$/ });
    await user.clear(sReview);
    await user.type(sReview, "0.5");
    await user.click(t.getByRole("button", { name: "Save Thresholds" }));
    await waitFor(() => expect(puts("/api/settings/thresholds")).toHaveLength(1));
    expect(puts("/api/settings/thresholds")[0]?.body).toMatchObject({ S: { review: 0.5 }, L: { auto: 0.45, review: 0.32 } });
    expect(puts("/api/settings/thresholds")[0]?.body).not.toHaveProperty("auto_floor");
  });

  it("retention: validates days and saves", async () => {
    const user = userEvent.setup();
    routes["PUT /api/settings/retention"] = (c) => json(200, c.body);
    renderPage();
    const r = await section("retention");
    const input = r.getByLabelText("Unmatched raw photos (days)");
    await user.clear(input);
    expect(r.getByText(/whole number of days/)).toBeInTheDocument();
    expect(r.getByRole("button", { name: "Save Retention" })).toBeDisabled();
    await user.type(input, "14");
    await user.click(r.getByRole("button", { name: "Save Retention" }));
    await waitFor(() => expect(puts("/api/settings/retention")[0]?.body).toEqual({ raw_days: 14, face_days: 30 }));
  });

  it("system: shows model info, never secrets, and repairs the webhook only after confirming", async () => {
    const user = userEvent.setup();
    routes["POST /api/openwa/webhook/register"] = () => json(200, { action: "updated", id: "w1", url: "http://kino-api:8080/api/webhook/openwa", events: [] });
    renderPage();
    const sys = await section("system");
    expect(sys.getAllByText("auraface").length).toBeGreaterThan(0);
    expect(sys.getByText("License: MIT")).toBeInTheDocument();
    expect(await sys.findByText("Registered")).toBeInTheDocument();
    await user.click(sys.getByRole("button", { name: "Repair webhook" }));
    const dialog = await screen.findByRole("dialog");
    expect(calls.some((c) => c.url === "/api/openwa/webhook/register")).toBe(false);
    await user.click(within(dialog).getByRole("button", { name: "Repair webhook" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.url === "/api/openwa/webhook/register")).toBe(true));
    expect(await screen.findByText("Webhook updated")).toBeInTheDocument();
  });

  it("system: shows a webhook error with retry when OpenWA is unreachable", async () => {
    routes["GET /api/openwa/webhook"] = () => json(502, { detail: "OpenWA unreachable" });
    renderPage();
    const sys = await section("system");
    expect(await sys.findByText(/OpenWA unreachable/)).toBeInTheDocument();
    expect(sys.getByRole("button", { name: /Retry/ })).toBeInTheDocument();
  });

  it("openwa: shows the saved connection without secrets and links to the wizard", async () => {
    renderPage();
    const s = await section("openwa");
    expect(await s.findByText("http://openwa:2785")).toBeInTheDocument();
    expect(s.getByText("Main phone (main)")).toHaveAttribute("dir", "auto");
    expect(s.getAllByText("Saved")).toHaveLength(2);
    expect(s.getByRole("link", { name: /Reconnect/ })).toHaveAttribute("href", "/setup/openwa");
  });

  it("openwa: marks env-locked fields read-only", async () => {
    setupStatus = { ...setupStatus, env_overrides: ["url", "webhook_secret"] };
    renderPage();
    const s = await section("openwa");
    expect(await s.findByText(/KINO_OPENWA_URL/)).toBeInTheDocument();
    expect(s.getByText(/KINO_WEBHOOK_SECRET/)).toBeInTheDocument();
  });

  it("openwa: shows when nothing is configured", async () => {
    setupStatus = { ...setupStatus, configured: false, url: null, session_id: null, session_name: null, has_api_key: false, has_webhook_secret: false };
    renderPage();
    const s = await section("openwa");
    expect(await s.findByText("Not configured")).toBeInTheDocument();
    expect(s.getAllByText("Not set").length).toBeGreaterThanOrEqual(4);
  });
});
