import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeProvider } from "@/components/theme-provider";
import { Toaster } from "@/components/ui/sonner";
import { createQueryClient } from "@/lib/query";
import type { TelegramChat, TelegramSettings } from "@/lib/telegram";
import { TelegramSection } from "./TelegramSection";

type Call = { method: string; url: string; body: unknown };
let routes: Record<string, (c: Call) => Response>;
let calls: Call[];
let chats: TelegramChat[];
const json = (code: number, body: unknown) =>
  new Response(JSON.stringify(body), { status: code, headers: { "Content-Type": "application/json" } });
const base: TelegramSettings = {
  has_token: true, bot_username: "kino_bot", api_url: "https://api.telegram.org", rate_per_min: 20,
  send_as_file: false, delete_on_remove: true,
};

beforeEach(() => {
  document.cookie = "kino_csrf=tok; path=/";
  calls = [];
  chats = [{ id: 7, chat_id: "-100123", title: "Family", type: "group", linked_at: "2026-10-01T00:00:00" }];
  routes = {
    "GET /api/telegram/chats": () => json(200, chats),
    "PUT /api/settings/telegram": (c) => json(200, { ...base, ...(c.body as object), bot_token: undefined }),
    "POST /api/telegram/test": () => json(200, { ok: true, bot_username: "kino_bot" }),
    "DELETE /api/telegram/chats/7": () => new Response(null, { status: 204 }),
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

function renderIt(saved: Partial<TelegramSettings> = {}) {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <ThemeProvider>
        <TelegramSection saved={{ ...base, ...saved }} />
        <Toaster />
      </ThemeProvider>
    </QueryClientProvider>,
  );
}
const puts = () => calls.filter((c) => c.method === "PUT");

describe("TelegramSection", () => {
  it("masks the token and never shows it", async () => {
    renderIt();
    const input = screen.getByLabelText("Bot token");
    expect(input).toHaveAttribute("type", "password");
    expect(input).toHaveValue("");
    expect(input).toHaveAttribute("placeholder", "•••• saved");
    expect(screen.getByText("Configured")).toBeInTheDocument();
    expect(await screen.findByText("Family")).toBeInTheDocument();
  });

  it("always renders an editable token input and the new fields", () => {
    renderIt();
    expect(screen.getByLabelText("Bot token")).toBeEnabled();
    expect(screen.queryByText(/KINO_TELEGRAM/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("Bot API URL")).toHaveValue("https://api.telegram.org");
    expect(screen.getByLabelText("Send rate (messages per minute)")).toHaveValue(20);
    expect(screen.getByText(/bot token is sent to this URL/)).toBeInTheDocument();
  });

  it("renders defaults when api_url and rate_per_min are missing (old payload)", () => {
    const old = { ...base } as Partial<TelegramSettings>;
    delete old.api_url;
    delete old.rate_per_min;
    renderIt(old as TelegramSettings);
    expect(screen.getByLabelText("Bot API URL")).toHaveValue("https://api.telegram.org");
    expect(screen.getByLabelText("Send rate (messages per minute)")).toHaveValue(20);
  });

  it("sends only api_url and rate_per_min when those change", async () => {
    const user = userEvent.setup();
    renderIt();
    const url = screen.getByLabelText("Bot API URL");
    await user.clear(url);
    await user.type(url, "http://botapi:8081");
    const rate = screen.getByLabelText("Send rate (messages per minute)");
    await user.clear(rate);
    await user.type(rate, "30");
    await user.click(screen.getByRole("button", { name: "Save Telegram" }));
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0]?.body).toEqual({ api_url: "http://botapi:8081", rate_per_min: 30 });
  });

  it("blocks saving an empty or out-of-range rate", async () => {
    const user = userEvent.setup();
    renderIt();
    const rate = screen.getByLabelText("Send rate (messages per minute)");
    await user.clear(rate);
    expect(screen.getByRole("button", { name: "Save Telegram" })).toBeDisabled();
    await user.type(rate, "601");
    expect(screen.getByRole("button", { name: "Save Telegram" })).toBeDisabled();
  });

  it("shows server field errors for api_url and rate_per_min", async () => {
    const user = userEvent.setup();
    routes["PUT /api/settings/telegram"] = () =>
      json(422, { detail: [
        { loc: ["body", "api_url"], msg: "must be http(s)" },
        { loc: ["body", "rate_per_min"], msg: "too high" },
      ] });
    renderIt();
    const url = screen.getByLabelText("Bot API URL");
    await user.clear(url);
    await user.type(url, "ftp://x");
    await user.click(screen.getByRole("button", { name: "Save Telegram" }));
    expect((await screen.findAllByText(/must be http\(s\)/)).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/too high/).length).toBeGreaterThan(0);
    expect(screen.getByLabelText("Bot API URL")).toHaveAttribute("aria-invalid", "true");
  });

  it("sends only changed fields", async () => {
    const user = userEvent.setup();
    renderIt();
    await user.click(screen.getByRole("switch", { name: "Send as file" }));
    await user.click(screen.getByRole("button", { name: "Save Telegram" }));
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0]?.body).toEqual({ send_as_file: true });

    await user.type(screen.getByLabelText("Bot token"), " 123:abc ");
    await user.click(screen.getByRole("button", { name: "Save Telegram" }));
    await waitFor(() => expect(puts()).toHaveLength(2));
    expect(puts()[1]?.body).toEqual({ bot_token: "123:abc" });
  });

  it("toggles the delete option", async () => {
    const user = userEvent.setup();
    renderIt();
    await user.click(screen.getByRole("switch", { name: "Delete Telegram message on remove" }));
    await user.click(screen.getByRole("button", { name: "Save Telegram" }));
    await waitFor(() => expect(puts()[0]?.body).toEqual({ delete_on_remove: false }));
  });

  it("clears the token after confirmation", async () => {
    const user = userEvent.setup();
    renderIt();
    await user.click(screen.getByRole("button", { name: "Clear token" }));
    expect(puts()).toHaveLength(0);
    const dlg = await screen.findByRole("dialog");
    await user.click(within(dlg).getByRole("button", { name: "Clear token" }));
    await waitFor(() => expect(puts()).toHaveLength(1));
    expect(puts()[0]?.body).toEqual({ bot_token: null });
  });

  it("shows the bot name after Test", async () => {
    const user = userEvent.setup();
    renderIt();
    await user.click(screen.getByRole("button", { name: "Test" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Connected as @kino_bot");
  });

  it("shows plain Connected when the bot username is unknown", async () => {
    routes["POST /api/telegram/test"] = () => json(200, { ok: true, bot_username: null });
    const user = userEvent.setup();
    renderIt();
    await user.click(screen.getByRole("button", { name: "Test" }));
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent(/^Connected$/);
    expect(status).not.toHaveTextContent("@");
  });

  it("shows the 409 in-use message when unlinking", async () => {
    routes["DELETE /api/telegram/chats/7"] = () => json(409, { detail: "Chat is used as a forwarding destination" });
    const user = userEvent.setup();
    renderIt();
    await user.click(await screen.findByRole("button", { name: "Unlink Family" }));
    const dlg = await screen.findByRole("dialog");
    await user.click(within(dlg).getByRole("button", { name: "Unlink" }));
    expect(await screen.findByText("Chat is used as a forwarding destination")).toBeInTheDocument();
  });

  it("disables linking without a token", () => {
    renderIt({ has_token: false });
    expect(screen.getByText("Not configured")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Link a chat/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Test" })).toBeDisabled();
    expect(calls.some((c) => c.url === "/api/telegram/chats")).toBe(false);
  });
});
