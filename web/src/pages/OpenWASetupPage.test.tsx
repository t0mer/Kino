import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeProvider } from "@/components/theme-provider";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { OpenWASetupStatus } from "@/lib/openwaSetup";
import { createQueryClient } from "@/lib/query";
import { OpenWASetupPage } from "./OpenWASetupPage";

type Call = { method: string; url: string; body: unknown };
let routes: Record<string, (c: Call) => Response>;
let calls: Call[];
let status: OpenWASetupStatus;
const json = (code: number, body: unknown) =>
  new Response(JSON.stringify(body), { status: code, headers: { "Content-Type": "application/json" } });
const HOOK = "http://kino-api:8080/api/webhook/openwa";
const emptyStatus = (): OpenWASetupStatus => ({
  configured: false, url: null, session_id: null, session_name: null, has_api_key: false,
  has_webhook_secret: false, webhook_secret_pending: false, webhook_url: HOOK, env_overrides: [],
});
const SESSIONS = [
  { id: "main", name: "Main phone", status: "ready", phone_masked: "9725****567" },
  { id: "spare", name: "טלפון עבודה", status: "qr", phone_masked: null },
];

beforeEach(() => {
  document.cookie = "kino_csrf=tok; path=/";
  calls = [];
  status = emptyStatus();
  routes = {
    "GET /api/openwa/setup/status": () => json(200, status),
    "POST /api/openwa/setup/connect": () => json(200, { sessions: SESSIONS, generated_webhook_secret: false }),
    "POST /api/openwa/setup/session": (c) =>
      json(200, {
        session_id: (c.body as { session_id: string }).session_id, registered: true, action: "created",
        webhook_id: "h-1", webhook_url: HOOK, error: null,
      }),
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
  render(
    <QueryClientProvider client={createQueryClient()}>
      <ThemeProvider>
        <TooltipProvider>
          <MemoryRouter initialEntries={["/setup/openwa"]}>
            <Routes>
              <Route path="/setup/openwa" element={<OpenWASetupPage />} />
              <Route path="/review" element={<p>review page</p>} />
              <Route path="/settings" element={<p>settings page</p>} />
            </Routes>
          </MemoryRouter>
          <Toaster />
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>,
  );
}
const posts = (path: string) => calls.filter((c) => c.method === "POST" && c.url === path);

async function fillAndConnect(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByLabelText("OpenWA URL"), "http://openwa:2785");
  await user.type(screen.getByLabelText("API key"), "owa-key-123");
  await user.type(screen.getByLabelText("Webhook secret"), "a-long-enough-webhook-secret");
  await user.click(screen.getByRole("button", { name: "Test and continue" }));
}

describe("OpenWASetupPage", () => {
  it("connects, picks a session and registers the webhook", async () => {
    const user = userEvent.setup();
    renderPage();
    await fillAndConnect(user);
    const main = await screen.findByRole("radio", { name: /Main phone/ });
    expect(main).toBeChecked();
    expect(screen.getByText(/9725\*\*\*\*567/)).toBeInTheDocument();
    expect(screen.getByText("טלפון עבודה")).toHaveAttribute("dir", "auto");
    expect(screen.getByText(/Not connected yet/)).toBeInTheDocument();
    expect(posts("/api/openwa/setup/connect")[0]?.body).toEqual({
      url: "http://openwa:2785", api_key: "owa-key-123", webhook_secret: "a-long-enough-webhook-secret",
    });
    await user.click(screen.getByRole("button", { name: "Use this session" }));
    expect(await screen.findByText("Webhook created")).toBeInTheDocument();
    expect(posts("/api/openwa/setup/session")[0]?.body).toEqual({ session_id: "main" });
    await user.click(screen.getByRole("button", { name: "Open Kino" }));
    expect(await screen.findByText("review page")).toBeInTheDocument();
  });

  it("validates before calling the API", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "Test and continue" }));
    expect(screen.getByText("Enter the OpenWA URL.")).toBeInTheDocument();
    expect(screen.getByText("Enter the OpenWA API key.")).toBeInTheDocument();
    await user.type(screen.getByLabelText("OpenWA URL"), "ftp://x");
    await user.type(screen.getByLabelText("Webhook secret"), "short");
    expect(screen.getByText(/http:\/\/ or https:\/\//)).toBeInTheDocument();
    expect(screen.getByText(/at least 16/)).toBeInTheDocument();
    expect(posts("/api/openwa/setup/connect")).toHaveLength(0);
  });

  it("generates a webhook secret and toggles its visibility", async () => {
    const user = userEvent.setup();
    renderPage();
    const input = await screen.findByLabelText("Webhook secret");
    expect(input).toHaveAttribute("type", "password");
    await user.click(screen.getByRole("button", { name: "Generate" }));
    expect((input as HTMLInputElement).value).toMatch(/^[0-9a-f]{64}$/);
    expect(input).toHaveAttribute("type", "text");
    await user.click(screen.getByRole("button", { name: "Hide webhook secret" }));
    expect(input).toHaveAttribute("type", "password");
  });

  it("shows the server's error for a refused key and stays on Connect", async () => {
    routes["POST /api/openwa/setup/connect"] = () =>
      json(400, { detail: "OpenWA refused the request (HTTP 401): check the API key." });
    const user = userEvent.setup();
    renderPage();
    await fillAndConnect(user);
    expect(await screen.findByRole("alert")).toHaveTextContent("check the API key");
    expect(screen.getByLabelText("OpenWA URL")).toBeInTheDocument();
  });

  it("shows env-locked fields read-only and leaves them out of the request", async () => {
    status = { ...emptyStatus(), url: "http://openwa:2785", has_webhook_secret: true, env_overrides: ["url", "webhook_secret"] };
    const user = userEvent.setup();
    renderPage();
    const url = await screen.findByLabelText("OpenWA URL");
    expect(url).toBeDisabled();
    expect(url).toHaveValue("Set by KINO_OPENWA_URL");
    expect(screen.getByLabelText("Webhook secret")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Generate" })).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("API key"), "owa-key-123");
    await user.click(screen.getByRole("button", { name: "Test and continue" }));
    await screen.findByRole("radio", { name: /Main phone/ });
    expect(posts("/api/openwa/setup/connect")[0]?.body).toEqual({ api_key: "owa-key-123" });
  });

  it("offers a retry when the webhook registration fails", async () => {
    routes["POST /api/openwa/setup/session"] = () =>
      json(200, {
        session_id: "main", registered: false, action: null, webhook_id: null, webhook_url: HOOK,
        error: "Session saved, but OpenWA refused the webhook (HTTP 500).",
      });
    routes["POST /api/openwa/webhook/register"] = () =>
      json(200, { action: "created", id: "h-2", url: HOOK, events: ["message.received"] });
    const user = userEvent.setup();
    renderPage();
    await fillAndConnect(user);
    await user.click(await screen.findByRole("button", { name: "Use this session" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("HTTP 500");
    await user.click(screen.getByRole("button", { name: "Retry registration" }));
    expect(await screen.findByText("Webhook created")).toBeInTheDocument();
    expect(posts("/api/openwa/webhook/register")[0]?.body).toEqual({ url: HOOK });
  });

  it("warns on the Session step that a new webhook secret is not applied yet", async () => {
    const user = userEvent.setup();
    renderPage();
    await fillAndConnect(user);
    await screen.findByRole("radio", { name: /Main phone/ });
    expect(screen.queryByText(/New webhook secret not applied yet/)).not.toBeInTheDocument();
    status = { ...status, configured: true, has_webhook_secret: true, webhook_secret_pending: true };
    await user.click(screen.getByRole("button", { name: "Back" }));
    await user.click(screen.getByRole("button", { name: "Test and continue" }));
    expect(await screen.findByText(/New webhook secret not applied yet/)).toBeInTheDocument();
  });

  it("explains an empty session list", async () => {
    routes["POST /api/openwa/setup/connect"] = () => json(200, { sessions: [], generated_webhook_secret: false });
    const user = userEvent.setup();
    renderPage();
    await fillAndConnect(user);
    expect(await screen.findByText(/no sessions yet/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reload sessions" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Use this session" })).toBeDisabled();
  });

  it("recovers from a failed status fetch with Try again", async () => {
    let fail = true;
    routes["GET /api/openwa/setup/status"] = () => (fail ? json(500, { detail: "Server exploded." }) : json(200, status));
    const user = userEvent.setup();
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("Server exploded.");
    fail = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByLabelText("OpenWA URL")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("skips the session pick and only registers when the session is env-locked", async () => {
    status = { ...emptyStatus(), session_id: "main", env_overrides: ["session_id"] };
    routes["POST /api/openwa/webhook/register"] = () =>
      json(200, { action: "created", id: "h-3", url: HOOK, events: ["message.received"] });
    const user = userEvent.setup();
    renderPage();
    await user.type(await screen.findByLabelText("OpenWA URL"), "http://openwa:2785");
    await user.type(screen.getByLabelText("API key"), "owa-key-123");
    await user.click(screen.getByRole("button", { name: "Test and continue" }));
    expect(await screen.findByText(/KINO_OPENWA_SESSION_ID/)).toBeInTheDocument();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Register webhook" }));
    expect(await screen.findByText("Webhook created")).toBeInTheDocument();
    expect(posts("/api/openwa/setup/session")).toHaveLength(0);
    expect(posts("/api/openwa/webhook/register")[0]?.body).toEqual({ url: HOOK });
  });

  it("shows a locked API key read-only and omits it from the request", async () => {
    status = { ...emptyStatus(), has_api_key: true, env_overrides: ["api_key"] };
    const user = userEvent.setup();
    renderPage();
    const key = await screen.findByLabelText("API key");
    expect(key).toBeDisabled();
    expect(key).toHaveValue("Set by KINO_OPENWA_API_KEY");
    await user.type(screen.getByLabelText("OpenWA URL"), "http://openwa:2785");
    await user.click(screen.getByRole("button", { name: "Test and continue" }));
    await screen.findByRole("radio", { name: /Main phone/ });
    expect(posts("/api/openwa/setup/connect")[0]?.body).toEqual({ url: "http://openwa:2785" });
  });

  it("reloads the sessions when the picked session no longer exists (404)", async () => {
    routes["POST /api/openwa/setup/session"] = () => json(404, { detail: "Session not found." });
    const user = userEvent.setup();
    renderPage();
    await fillAndConnect(user);
    await user.click(await screen.findByRole("button", { name: "Use this session" }));
    await vi.waitFor(() => expect(posts("/api/openwa/setup/connect")).toHaveLength(2));
    expect(await screen.findByRole("alert")).toHaveTextContent("Session not found.");
    expect(screen.getByRole("radio", { name: /Main phone/ })).toBeInTheDocument();
  });

  it.each([
    [409, "Conflict from the server."],
    [422, "Invalid request."],
    [502, "OpenWA is unreachable."],
  ])("shows the server's message for a %i on connect", async (code, detail) => {
    routes["POST /api/openwa/setup/connect"] = () => json(code, { detail });
    const user = userEvent.setup();
    renderPage();
    await fillAndConnect(user);
    expect(await screen.findByRole("alert")).toHaveTextContent(detail);
    expect(screen.getByLabelText("OpenWA URL")).toBeInTheDocument();
  });
});
