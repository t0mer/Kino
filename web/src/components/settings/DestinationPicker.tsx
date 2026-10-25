import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { cn } from "@/lib/utils";
import { settingsApi, settingsKeys } from "@/lib/settings";
import { type Channel, type Destination, telegramApi, telegramKeys } from "@/lib/telegram";
import { ChatPicker, useChatNames } from "./ChatPicker";

const CHANNEL_LABEL: Record<Channel, string> = { whatsapp: "WhatsApp", telegram: "Telegram" };
const TELEGRAM_HINT = "Telegram isn't set up yet — configure the bot and link a chat in the";
export const TELEGRAM_UNCONFIGURED_HINT = `${TELEGRAM_HINT} Telegram section`;

/** A channel-aware destination field: pick WhatsApp or Telegram, then a chat on that channel. */
export function DestinationPicker({
  id,
  label,
  value,
  onChange,
  allowedChannels = ["whatsapp", "telegram"],
  emptyLabel = "Not set",
  error,
}: {
  id: string;
  label: string;
  value: Destination | null;
  onChange: (d: Destination | null) => void;
  allowedChannels?: Channel[];
  emptyLabel?: string;
  error?: string;
}) {
  const names = useChatNames();
  const settings = useQuery({ queryKey: settingsKeys.all, queryFn: settingsApi.get, staleTime: Infinity });
  const chatsQ = useQuery({ queryKey: telegramKeys.chats, queryFn: telegramApi.chats, retry: false });
  const tgChats = chatsQ.data ?? [];
  const hasToken = settings.data?.telegram?.has_token ?? false;
  const telegramUnavailable = !hasToken && tgChats.length === 0;

  const [picked, setPicked] = useState<Channel>(value?.channel ?? allowedChannels[0] ?? "whatsapp");
  const channel = value?.channel ?? picked;

  const choose = (c: Channel) => {
    if (c === channel) return;
    setPicked(c);
    if (value) onChange(null);
  };

  return (
    <div className="space-y-2">
      {allowedChannels.length > 1 && (
        <div role="group" aria-label={`${label} channel`} className="inline-flex rounded-md border p-0.5">
          {allowedChannels.map((c) => {
            const disabled = c === "telegram" && telegramUnavailable;
            return (
              <button
                key={c}
                type="button"
                aria-pressed={channel === c}
                disabled={disabled}
                title={disabled ? TELEGRAM_UNCONFIGURED_HINT : undefined}
                onClick={() => choose(c)}
                className={cn(
                  "rounded px-3 py-1 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
                  channel === c ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-accent",
                )}
              >
                {CHANNEL_LABEL[c]}
              </button>
            );
          })}
        </div>
      )}

      {channel === "whatsapp" ? (
        <ChatPicker
          id={id}
          label={label}
          value={value?.channel === "whatsapp" ? value.chat_id : null}
          names={names}
          emptyLabel={emptyLabel}
          error={error}
          onChange={(chat) => onChange(chat ? { channel: "whatsapp", chat_id: chat } : null)}
        />
      ) : (
        <div className="space-y-1">
          <select
            id={id}
            aria-label={label}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? `${id}-error` : undefined}
            className="h-9 w-full rounded-md border bg-background px-2 text-sm"
            value={value?.channel === "telegram" ? value.chat_id : ""}
            onChange={(e) => onChange(e.target.value ? { channel: "telegram", chat_id: e.target.value } : null)}
          >
            <option value="">{emptyLabel}</option>
            {value?.channel === "telegram" && !tgChats.some((c) => c.chat_id === value.chat_id) && (
              <option value={value.chat_id}>{value.chat_id}</option>
            )}
            {tgChats.map((c) => (
              <option key={c.id} value={c.chat_id}>
                {c.title} ({c.type})
              </option>
            ))}
          </select>
          {tgChats.length === 0 && !chatsQ.isPending && (
            <p className="text-xs text-muted-foreground">
              {TELEGRAM_HINT}{" "}
              <a href="#telegram" className="underline underline-offset-2">
                Telegram section
              </a>
            </p>
          )}
        </div>
      )}
    </div>
  );
}
