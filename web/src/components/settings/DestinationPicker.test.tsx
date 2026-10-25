import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createQueryClient } from "@/lib/query";
import type { Destination, TelegramChat } from "@/lib/telegram";
import { DestinationPicker } from "./DestinationPicker";

let routes: Record<string, () => Response>;
let hasToken: boolean;
let chats: TelegramChat[];
const json = (code: number, body: unknown) =>
  new Response(JSON.stringify(body), { status: code, headers: { "Content-Type": "application/json" } });

beforeEach(() => {
  hasToken = true;
  chats = [{ id: 1, chat_id: "-100", title: "Family", type: "group", linked_at: "x" }];
  routes = {
    "GET /api/settings": () => json(200, { telegram: { has_token: hasToken } }),
    "GET /api/telegram/chats": () => json(200, chats),
    "GET /api/openwa/chats": () =>
      json(200, [{ id: "1@g.us", name: "Class", is_group: true, watched: false, selectable: true }]),
  };
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const h = routes[`${(init.method ?? "GET").toUpperCase()} ${url.split("?")[0]}`];
    return h ? h() : json(404, { detail: "nf" });
  }));
});
afterEach(() => vi.unstubAllGlobals());

const seen: (Destination | null)[] = [];
function Harness({ initial = null }: { initial?: Destination | null }) {
  const [v, setV] = useState<Destination | null>(initial);
  return (
    <DestinationPicker id="d" label="Dest" value={v} onChange={(d) => { seen.push(d); setV(d); }} emptyLabel="Pick one" />
  );
}
const renderIt = (initial?: Destination | null) => {
  seen.length = 0;
  return render(<QueryClientProvider client={createQueryClient()}><Harness initial={initial} /></QueryClientProvider>);
};

describe("DestinationPicker", () => {
  it("uses the WhatsApp ChatPicker by default and emits a whatsapp destination", async () => {
    const user = userEvent.setup();
    renderIt();
    await user.click(screen.getByRole("button", { name: /Dest: Pick one\. Change/ }));
    await user.click(await screen.findByRole("button", { name: /Class/ }));
    expect(seen.at(-1)).toEqual({ channel: "whatsapp", chat_id: "1@g.us" });
  });

  it("lists linked Telegram chats and emits a telegram destination", async () => {
    const user = userEvent.setup();
    renderIt();
    const tg = screen.getByRole("button", { name: "Telegram" });
    await vi.waitFor(() => expect(tg).toBeEnabled());
    await user.click(tg);
    const sel = screen.getByLabelText("Dest");
    await within(sel).findByRole("option", { name: "Family (group)" });
    await user.selectOptions(sel, "-100");
    expect(seen.at(-1)).toEqual({ channel: "telegram", chat_id: "-100" });
  });

  it("clears the chat id when the channel changes", async () => {
    const user = userEvent.setup();
    renderIt({ channel: "whatsapp", chat_id: "1@g.us" });
    const tg = screen.getByRole("button", { name: "Telegram" });
    await vi.waitFor(() => expect(tg).toBeEnabled());
    await user.click(tg);
    expect(seen.at(-1)).toBeNull();
    expect(screen.getByLabelText("Dest")).toHaveValue("");
  });

  it("disables Telegram and hints when there is no token and no chats", async () => {
    hasToken = false;
    chats = [];
    renderIt();
    const tg = screen.getByRole("button", { name: "Telegram" });
    await vi.waitFor(() => expect(tg).toBeDisabled());
    expect(tg).toHaveAttribute("title", expect.stringContaining("isn't set up yet"));
  });

  it("shows a link to the Telegram section when a token exists but no chat is linked", async () => {
    chats = [];
    const user = userEvent.setup();
    renderIt({ channel: "telegram", chat_id: "-5" });
    expect(await screen.findByRole("link", { name: "Telegram section" })).toHaveAttribute("href", "#telegram");
    await user.click(screen.getByRole("button", { name: "WhatsApp" }));
    expect(seen.at(-1)).toBeNull();
  });
});
