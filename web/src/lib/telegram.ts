/** Telegram delivery API (settings, chat linking) and destination helpers. */

import { api } from "./api";

export type Channel = "whatsapp" | "telegram";

export interface Destination {
  channel: Channel;
  chat_id: string;
}

export type TelegramField = "bot_token";

export interface TelegramSettings {
  has_token: boolean;
  bot_username: string | null;
  api_url: string;
  rate_per_min: number;
  send_as_file: boolean;
  delete_on_remove: boolean;
  /** Fields locked because the matching KINO_* env var is set. */
  env_overrides: TelegramField[];
}

export interface TelegramSettingsPut {
  /** Omit = unchanged; null or "" = clear. */
  bot_token?: string | null;
  send_as_file?: boolean;
  delete_on_remove?: boolean;
}

export type TelegramChatType = "private" | "group" | "supergroup" | "channel";

export interface TelegramChat {
  id: number;
  chat_id: string;
  title: string;
  type: TelegramChatType;
  linked_at: string;
}

export interface LinkStart {
  code: string;
  bot_username: string | null;
  expires_in: number;
  instructions: string;
}

export interface LinkStatus {
  status: "pending" | "linked" | "expired";
  chat: TelegramChat | null;
}

export const TELEGRAM_ENV_NAMES: Record<TelegramField, string> = {
  bot_token: "KINO_TELEGRAM_BOT_TOKEN",
};

export const telegramKeys = {
  chats: ["telegram", "chats"] as const,
  settings: ["telegram", "settings"] as const,
};

export const telegramApi = {
  getSettings: () => api.get<TelegramSettings>("/api/settings/telegram"),
  putSettings: (b: TelegramSettingsPut) => api.put<TelegramSettings>("/api/settings/telegram", b),
  test: () => api.post<{ ok: boolean; bot_username: string | null }>("/api/telegram/test", {}),
  startLink: () => api.post<LinkStart>("/api/telegram/link", {}),
  /** Long-polls on the server for up to ~8 s. */
  pollLink: (code: string, signal?: AbortSignal) =>
    api.get<LinkStatus>(`/api/telegram/link/${encodeURIComponent(code)}`, { signal }),
  chats: () => api.get<TelegramChat[]>("/api/telegram/chats"),
  deleteChat: (id: number) => api.delete<void>(`/api/telegram/chats/${id}`),
};

export function sameDestination(a: Destination | null | undefined, b: Destination | null | undefined): boolean {
  if (!a || !b) return !a && !b;
  return a.channel === b.channel && a.chat_id === b.chat_id;
}

/** Human label for a destination: chat title/name when known, else the raw id. */
export function destinationLabel(
  dest: Destination,
  whatsappNames: Map<string, string | null>,
  telegramChats: TelegramChat[],
): string {
  if (dest.channel === "telegram") {
    const t = telegramChats.find((c) => c.chat_id === dest.chat_id)?.title.trim();
    return `Telegram: ${t || dest.chat_id}`;
  }
  return `WhatsApp: ${whatsappNames.get(dest.chat_id)?.trim() || dest.chat_id}`;
}
