import { describe, expect, it } from "vitest";
import { destinationLabel, sameDestination, type TelegramChat } from "./telegram";

const chats: TelegramChat[] = [
  { id: 1, chat_id: "-100", title: "Family", type: "group", linked_at: "2026-10-01T00:00:00" },
];

describe("sameDestination", () => {
  it("compares channel and chat id, nulls equal each other", () => {
    expect(sameDestination(null, undefined)).toBe(true);
    expect(sameDestination(null, { channel: "telegram", chat_id: "1" })).toBe(false);
    expect(sameDestination({ channel: "telegram", chat_id: "1" }, { channel: "telegram", chat_id: "1" })).toBe(true);
    expect(sameDestination({ channel: "telegram", chat_id: "1" }, { channel: "whatsapp", chat_id: "1" })).toBe(false);
  });
});

describe("destinationLabel", () => {
  const names = new Map<string, string | null>([["1@g.us", "Class"], ["2@g.us", null]]);
  it("uses names and titles, falls back to ids", () => {
    expect(destinationLabel({ channel: "whatsapp", chat_id: "1@g.us" }, names, chats)).toBe("WhatsApp: Class");
    expect(destinationLabel({ channel: "whatsapp", chat_id: "2@g.us" }, names, chats)).toBe("WhatsApp: 2@g.us");
    expect(destinationLabel({ channel: "telegram", chat_id: "-100" }, names, chats)).toBe("Telegram: Family");
    expect(destinationLabel({ channel: "telegram", chat_id: "-999" }, names, chats)).toBe("Telegram: -999");
  });
});
