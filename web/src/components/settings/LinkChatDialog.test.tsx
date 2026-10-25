import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createQueryClient } from "@/lib/query";
import type { LinkStatus } from "@/lib/telegram";
import { LinkChatDialog } from "./LinkChatDialog";

let polls: number;
let queue: Array<() => Response | Promise<Response>>;
const json = (code: number, body: unknown) =>
  new Response(JSON.stringify(body), { status: code, headers: { "Content-Type": "application/json" } });
const status = (s: LinkStatus) => () => json(200, s);
const chat = { id: 1, chat_id: "-1", title: "Family", type: "group" as const, linked_at: "x" };

beforeEach(() => {
  document.cookie = "kino_csrf=tok; path=/";
  polls = 0;
  queue = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const method = (init.method ?? "GET").toUpperCase();
    if (method === "POST" && url === "/api/telegram/link")
      return json(200, { code: "ABC123", bot_username: "kino_bot", expires_in: 600, instructions: "" });
    if (url.startsWith("/api/telegram/link/")) {
      polls++;
      const next = queue.shift();
      return next ? next() : status({ status: "pending", chat: null })();
    }
    return json(404, { detail: "nf" });
  }));
});
afterEach(() => vi.unstubAllGlobals());

function Harness({ onClose }: { onClose?: () => void }) {
  const [open, setOpen] = useState(true);
  return (
    <>
      <button onClick={() => setOpen(true)}>reopen</button>
      <LinkChatDialog open={open} onOpenChange={(o) => { setOpen(o); if (!o) onClose?.(); }} />
    </>
  );
}
const renderIt = (onClose?: () => void) =>
  render(
    <QueryClientProvider client={createQueryClient()}>
      <Harness onClose={onClose} />
    </QueryClientProvider>,
  );

describe("LinkChatDialog", () => {
  it("shows the code and instructions while waiting", async () => {
    renderIt();
    expect(await screen.findByTestId("link-code")).toHaveTextContent("ABC123");
    expect(screen.getByText("@kino_bot")).toBeInTheDocument();
    expect(screen.getByText("/start ABC123")).toBeInTheDocument();
    expect(screen.getByText("Waiting for message…")).toBeInTheDocument();
  });

  it("shows the server instructions when the bot username is unknown", async () => {
    const orig = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) =>
      (init.method ?? "GET").toUpperCase() === "POST" && url === "/api/telegram/link"
        ? json(200, { code: "ABC123", bot_username: null, expires_in: 600, instructions: "Send /start ABC123 to your bot in a private chat." })
        : orig(url, init)));
    renderIt();
    expect(await screen.findByTestId("link-code")).toHaveTextContent("ABC123");
    expect(screen.getByText("Send /start ABC123 to your bot in a private chat.")).toBeInTheDocument();
    expect(screen.queryByText(/@null|^@$/)).toBeNull();
  });

  it("falls back to 'your bot' with no username and no instructions", async () => {
    const orig = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) =>
      (init.method ?? "GET").toUpperCase() === "POST" && url === "/api/telegram/link"
        ? json(200, { code: "ABC123", bot_username: null, expires_in: 600, instructions: "" })
        : orig(url, init)));
    renderIt();
    expect(await screen.findByText("your bot")).toBeInTheDocument();
  });

  it("polls repeatedly until linked, then closes", async () => {
    queue = [status({ status: "pending", chat: null }), status({ status: "pending", chat: null }), status({ status: "linked", chat })];
    const onClose = vi.fn();
    renderIt(onClose);
    expect(await screen.findByText("Family")).toBeInTheDocument();
    expect(polls).toBe(3);
    await waitFor(() => expect(onClose).toHaveBeenCalled(), { timeout: 3000 });
    const before = polls;
    await new Promise((r) => setTimeout(r, 100));
    expect(polls).toBe(before);
  });

  it("offers a new code when expired", async () => {
    queue = [status({ status: "expired", chat: null })];
    const user = userEvent.setup();
    renderIt();
    await screen.findByText("This code expired.");
    await user.click(screen.getByRole("button", { name: "Generate new code" }));
    expect(await screen.findByTestId("link-code")).toBeInTheDocument();
  });

  it("stops polling when closed", async () => {
    let release: (r: Response) => void = () => {};
    queue = [() => new Promise<Response>((res) => { release = res; })];
    const user = userEvent.setup();
    renderIt();
    await screen.findByTestId("link-code");
    await waitFor(() => expect(polls).toBe(1));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    release(status({ status: "pending", chat: null })());
    await new Promise((r) => setTimeout(r, 100));
    expect(polls).toBe(1);
  });

  it("shows a definite API error and stops", async () => {
    queue = [() => json(409, { detail: "A webhook is set on this bot" })];
    renderIt();
    expect(await screen.findByRole("alert")).toHaveTextContent("A webhook is set on this bot");
    expect(polls).toBe(1);
  });
});
