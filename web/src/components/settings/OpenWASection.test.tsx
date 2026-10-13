import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeProvider } from "@/components/theme-provider";
import { Toaster } from "@/components/ui/sonner";
import { createQueryClient } from "@/lib/query";
import type { OpenWASession, OpenWASetupStatus } from "@/lib/openwaSetup";
import { OpenWASection } from "./OpenWASection";

type Call = { method: string; url: string; body: unknown };
let routes: Record<string, (c: Call) => Response>;
let calls: Call[];
let status: OpenWASetupStatus;
const json = (code: number, body: unknown) =>
  new Response(JSON.stringify(body), { status: code, headers: { "Content-Type": "application/json" } });
const sessions: OpenWASession[] = [
  { id: "main", name: "Main phone", status: "ready", phone_masked: "9725****123" },
  { id: "work", name: "Work phone", status: "qr", phone_masked: null },
  { id: "spare", name: "Spare", status: "ready", phone_masked: "9725****999" },
];

beforeEach(() => {
  document.cookie = "kino_csrf=tok; path=/";
  calls = [];
  status = {
    configured: true, url: "http://openwa:2785", session_id: "main", session_name: "Main phone",
    outgoing_session_id: null, outgoing_session_name: null, same_as_incoming: true,
    has_api_key: true, has_webhook_secret: true, webhook_secret_pending: false,
    webhook_url: "http://kino-api:8080/api/webhook/openwa", env_overrides: [],
  };
  routes = {
    "GET /api/openwa/setup/status": () => json(200, status),
    "GET /api/openwa/setup/sessions": () => json(200, sessions),
    "PUT /api/openwa/setup/outgoing-session": () => json(200, { outgoing_session_id: "spare", outgoing_session_name: "Spare", same_as_incoming: false }),
  };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = (init.method ?? "GET").toUpperCase();
    const call: Call = { method, url, body: init.body ? JSON.parse(String(init.body)) : undefined };
    calls.push(call);
    const h = routes[`${method} ${url.split("?")[0]}`];
    return h ? h(call) : json(404, { detail: "not found" });
  }));
});
afterEach(() => vi.unstubAllGlobals());

function renderIt() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <ThemeProvider>
        <MemoryRouter><OpenWASection /></MemoryRouter>
        <Toaster />
      </ThemeProvider>
    </QueryClientProvider>,
  );
}
const puts = () => calls.filter((c) => c.method === "PUT");

describe("OpenWASection sending session", () => {
  it("defaults to Same as incoming and does not list sessions until asked", async () => {
    renderIt();
    const sel = await screen.findByLabelText("Sending session");
    expect(sel).toHaveValue("__same__");
    expect(screen.getByRole("option", { name: "Same as incoming (Main phone)" })).toBeInTheDocument();
    expect(screen.getByText(/Incoming photos are always received on Main phone/)).toBeInTheDocument();
    expect(calls.some((c) => c.url === "/api/openwa/setup/sessions")).toBe(false);
  });

  it("loads the list and PUTs the chosen session id", async () => {
    const user = userEvent.setup();
    renderIt();
    await screen.findByLabelText("Sending session");
    await user.click(screen.getByRole("button", { name: "Load sessions" }));
    await screen.findByRole("option", { name: /Spare/ });
    await user.selectOptions(screen.getByLabelText("Sending session"), "spare");
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0]?.body).toEqual({ session_id: "spare" });
    expect(calls.filter((c) => c.url === "/api/openwa/setup/status").length).toBeGreaterThan(1);
  });

  it("PUTs null when choosing Same as incoming", async () => {
    status = { ...status, outgoing_session_id: "spare", outgoing_session_name: "Spare", same_as_incoming: false };
    const user = userEvent.setup();
    renderIt();
    const sel = await screen.findByLabelText("Sending session");
    await waitFor(() => expect(sel).toHaveValue("spare"));
    await user.selectOptions(sel, "__same__");
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0]?.body).toEqual({ session_id: null });
  });

  it("warns when the picked session is not ready", async () => {
    status = { ...status, outgoing_session_id: "work", outgoing_session_name: "Work phone", same_as_incoming: false };
    const user = userEvent.setup();
    renderIt();
    await screen.findByLabelText("Sending session");
    await user.click(screen.getByRole("button", { name: "Load sessions" }));
    expect(await screen.findByText(/not connected \(qr\)/)).toBeInTheDocument();
  });

  it("warns when the saved session no longer exists in OpenWA", async () => {
    status = { ...status, outgoing_session_id: "gone", outgoing_session_name: "Old", same_as_incoming: false };
    const user = userEvent.setup();
    renderIt();
    await screen.findByLabelText("Sending session");
    await user.click(screen.getByRole("button", { name: "Load sessions" }));
    expect(await screen.findByText(/no longer exists in OpenWA/)).toBeInTheDocument();
  });

  it("shows an error with retry when the list fails", async () => {
    routes["GET /api/openwa/setup/sessions"] = () => json(502, { detail: "OpenWA unreachable" });
    const user = userEvent.setup();
    renderIt();
    await screen.findByLabelText("Sending session");
    await user.click(screen.getByRole("button", { name: "Load sessions" }));
    expect(await screen.findByText(/Could not load sessions: OpenWA unreachable/)).toBeInTheDocument();
    routes["GET /api/openwa/setup/sessions"] = () => json(200, sessions);
    await user.click(screen.getByRole("button", { name: /Retry/ }));
    expect(await screen.findByRole("option", { name: /Spare/ })).toBeInTheDocument();
  });

  it("shows the server message when the PUT returns 404", async () => {
    routes["PUT /api/openwa/setup/outgoing-session"] = () => json(404, { detail: "Session not found" });
    const user = userEvent.setup();
    renderIt();
    await screen.findByLabelText("Sending session");
    await user.click(screen.getByRole("button", { name: "Load sessions" }));
    await screen.findByRole("option", { name: /Spare/ });
    await user.selectOptions(screen.getByLabelText("Sending session"), "spare");
    expect((await screen.findAllByText(/Session not found/)).length).toBeGreaterThan(0);
  });
});
