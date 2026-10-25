import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link2, Loader2, Send, Unlink } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { toast } from "@/components/ui/sonner";
import { isApiError } from "@/lib/api";
import { type ParsedErrors, fieldError, parseFieldErrors, settingsKeys, type SettingsOut } from "@/lib/settings";
import {
  type TelegramChat,
  type TelegramSettings,
  type TelegramSettingsPut,
  TELEGRAM_DEFAULT_API_URL,
  TELEGRAM_DEFAULT_RATE,
  telegramApi,
  telegramKeys,
} from "@/lib/telegram";
import { LinkChatDialog } from "./LinkChatDialog";
import { ConfirmDialog, Field, NumberField, SectionCard } from "./SectionCard";

interface Draft {
  token: string;
  api_url: string;
  rate_per_min: number;
  send_as_file: boolean;
  delete_on_remove: boolean;
}

export function TelegramSection({ saved }: { saved: TelegramSettings }) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [errors, setErrors] = useState<ParsedErrors | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [linkOpen, setLinkOpen] = useState(false);
  const [unlinking, setUnlinking] = useState<TelegramChat | null>(null);
  const [unlinkError, setUnlinkError] = useState<string | null>(null);
  const [testedBot, setTestedBot] = useState<string | null>(null);

  const savedUrl = saved.api_url ?? TELEGRAM_DEFAULT_API_URL;
  const savedRate = saved.rate_per_min ?? TELEGRAM_DEFAULT_RATE;
  const base: Draft = {
    token: "",
    api_url: savedUrl,
    rate_per_min: savedRate,
    send_as_file: saved.send_as_file,
    delete_on_remove: saved.delete_on_remove,
  };
  const form = draft ?? base;
  const set = (patch: Partial<Draft>) => setDraft({ ...form, ...patch });
  const dirty =
    draft !== null &&
    (draft.token.trim() !== "" ||
      draft.api_url.trim() !== savedUrl ||
      draft.rate_per_min !== savedRate ||
      draft.send_as_file !== saved.send_as_file ||
      draft.delete_on_remove !== saved.delete_on_remove);

  const chats = useQuery({
    queryKey: telegramKeys.chats,
    queryFn: telegramApi.chats,
    enabled: saved.has_token,
    retry: false,
  });

  const applySaved = (out: TelegramSettings) => {
    qc.setQueryData<SettingsOut>(settingsKeys.all, (s) => (s ? { ...s, telegram: out } : s));
    qc.invalidateQueries({ queryKey: telegramKeys.chats });
    setDraft(null);
    setErrors(null);
    setTestedBot(null);
  };

  const save = useMutation({
    mutationFn: telegramApi.putSettings,
    onSuccess: (out) => {
      applySaved(out);
      toast.success("Telegram settings saved");
    },
    onError: (e) => {
      setErrors(parseFieldErrors(e.message));
      toast.error("Could not save Telegram settings", { description: e.message });
    },
  });

  const clear = useMutation({
    mutationFn: () => telegramApi.putSettings({ bot_token: null }),
    onSuccess: (out) => {
      applySaved(out);
      toast.success("Telegram token cleared");
    },
    onError: (e) => toast.error("Could not clear the token", { description: e.message }),
    onSettled: () => setConfirmClear(false),
  });

  const test = useMutation({
    mutationFn: telegramApi.test,
    onSuccess: (r) => {
      setTestedBot(r.bot_username ?? "");
      toast.success(r.bot_username ? `Connected as @${r.bot_username}` : "Connected");
    },
    onError: (e) => {
      setTestedBot(null);
      toast.error("Telegram test failed", { description: e.message });
    },
  });

  const unlink = useMutation({
    mutationFn: (c: TelegramChat) => telegramApi.deleteChat(c.id),
    onSuccess: () => {
      setUnlinking(null);
      setUnlinkError(null);
      qc.invalidateQueries({ queryKey: telegramKeys.chats });
      toast.success("Chat unlinked");
    },
    onError: (e) => {
      setUnlinking(null);
      setUnlinkError(isApiError(e, 409) ? e.message : `Could not unlink: ${e.message}`);
    },
  });

  const onSave = () => {
    const body: TelegramSettingsPut = {};
    if (form.token.trim() !== "") body.bot_token = form.token.trim();
    if (form.api_url.trim() !== savedUrl) body.api_url = form.api_url.trim();
    if (form.rate_per_min !== savedRate) body.rate_per_min = form.rate_per_min;
    if (form.send_as_file !== saved.send_as_file) body.send_as_file = form.send_as_file;
    if (form.delete_on_remove !== saved.delete_on_remove) body.delete_on_remove = form.delete_on_remove;
    save.mutate(body);
  };

  // The saved token is sent to the Bot API URL, so moving the URL needs the token re-entered.
  const urlChanged = form.api_url.trim() !== savedUrl;
  const needsToken = urlChanged && saved.has_token && form.token.trim() === "";

  const tokenError = errors ? fieldError(errors, "bot_token") : undefined;
  const urlError = errors ? fieldError(errors, "api_url") : undefined;
  const rateError = errors ? fieldError(errors, "rate_per_min") : undefined;
  const linked = chats.data ?? [];

  return (
    <div id="telegram">
      <SectionCard
        id="telegram"
        title="Telegram"
        description="Deliver saved photos to Telegram chats through your own bot."
        dirty={dirty}
        saving={save.isPending}
        canSave={!needsToken && Number.isInteger(form.rate_per_min) && form.rate_per_min >= 1 && form.rate_per_min <= 600}
        errors={errors}
        onSave={onSave}
        onReset={() => {
          setDraft(null);
          setErrors(null);
        }}
      >
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={saved.has_token ? "secondary" : "outline"}>
            {saved.has_token ? "Configured" : "Not configured"}
          </Badge>
          {saved.bot_username && <span className="text-sm text-muted-foreground">@{saved.bot_username}</span>}
        </div>

        <Field
          id="tg-token"
          label="Bot token"
          error={tokenError}
          hint={
            urlChanged && saved.has_token
              ? "Re-enter the bot token to change the URL — the token is sent to this address."
              : "Create a bot with @BotFather and paste its token. Leave empty to keep the saved token."
          }
        >
          <div className="flex flex-wrap items-center gap-2">
            <Input
              id="tg-token"
              type="password"
              autoComplete="off"
              className="min-w-0 flex-1"
              placeholder={saved.has_token ? "•••• saved" : "123456:ABC-DEF…"}
              aria-invalid={tokenError ? true : undefined}
              aria-describedby={tokenError ? "tg-token-error" : undefined}
              value={form.token}
              onChange={(e) => set({ token: e.target.value })}
            />
            {saved.has_token && (
              <Button type="button" variant="outline" onClick={() => setConfirmClear(true)}>
                Clear token
              </Button>
            )}
          </div>
        </Field>

        <Field
          id="tg-api-url"
          label="Bot API URL"
          error={urlError}
          hint="Leave as https://api.telegram.org unless you run a self-hosted Bot API server. The bot token is sent to this URL, so only use a server you trust."
        >
          <Input
            id="tg-api-url"
            type="url"
            autoComplete="off"
            aria-invalid={urlError ? true : undefined}
            aria-describedby={urlError ? "tg-api-url-error" : undefined}
            value={form.api_url}
            onChange={(e) => set({ api_url: e.target.value })}
          />
        </Field>

        <Field
          id="tg-rate"
          label="Send rate (messages per minute)"
          error={rateError}
          hint="Telegram limits groups to about 20 messages per minute."
        >
          <NumberField
            id="tg-rate"
            className="w-32"
            min={1}
            max={600}
            step={1}
            value={form.rate_per_min}
            error={rateError}
            onChange={(v) => set({ rate_per_min: v })}
          />
        </Field>

        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" variant="outline" disabled={!saved.has_token || test.isPending} onClick={() => test.mutate()}>
            {test.isPending ? <Loader2 className="animate-spin" /> : <Send />} Test
          </Button>
          {testedBot !== null && (
            <span role="status" className="text-sm text-muted-foreground">
              Connected{testedBot ? ` as @${testedBot}` : ""}
            </span>
          )}
        </div>

        <label className="flex items-center justify-between gap-3">
          <span className="text-sm font-medium">Send as file (original quality)</span>
          <Switch
            aria-label="Send as file"
            checked={form.send_as_file}
            onCheckedChange={(v) => set({ send_as_file: v })}
          />
        </label>
        <label className="flex items-center justify-between gap-3">
          <span className="text-sm font-medium">Delete Telegram message when a photo is removed from a kid</span>
          <Switch
            aria-label="Delete Telegram message on remove"
            checked={form.delete_on_remove}
            onCheckedChange={(v) => set({ delete_on_remove: v })}
          />
        </label>

        <div className="space-y-2 border-t pt-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm font-medium">Linked chats</p>
            <Button type="button" size="sm" disabled={!saved.has_token} onClick={() => setLinkOpen(true)}>
              <Link2 /> Link a chat
            </Button>
          </div>
          {!saved.has_token && (
            <p className="text-xs text-muted-foreground">Save a bot token to link chats.</p>
          )}
          {saved.has_token && chats.isError && (
            <p role="alert" className="text-sm text-destructive">
              Could not load linked chats: {chats.error.message}
            </p>
          )}
          {saved.has_token && chats.isSuccess && linked.length === 0 && (
            <p className="text-sm text-muted-foreground">No chats linked yet.</p>
          )}
          {linked.length > 0 && (
            <ul className="space-y-1" aria-label="Linked chats">
              {linked.map((c) => (
                <li key={c.id} className="flex items-center gap-3 rounded-md border px-3 py-2 text-sm">
                  <span className="min-w-0 flex-1">
                    <span className="block truncate" dir="auto">
                      {c.title}
                    </span>
                    <span className="block truncate text-xs text-muted-foreground">{c.chat_id}</span>
                  </span>
                  <Badge variant="outline">{c.type}</Badge>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    aria-label={`Unlink ${c.title}`}
                    onClick={() => {
                      setUnlinkError(null);
                      setUnlinking(c);
                    }}
                  >
                    <Unlink /> Unlink
                  </Button>
                </li>
              ))}
            </ul>
          )}
          {unlinkError && (
            <p role="alert" className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {unlinkError}
            </p>
          )}
        </div>

        <p className="text-xs text-muted-foreground">
          Photos sent to Telegram pass through Telegram&apos;s servers; bot chats are not end-to-end encrypted.
        </p>
      </SectionCard>

      <ConfirmDialog
        open={confirmClear}
        onOpenChange={setConfirmClear}
        title="Clear the Telegram token?"
        description="Kino stops delivering to Telegram until a new token is saved. Linked chats are kept."
        confirmLabel="Clear token"
        pending={clear.isPending}
        onConfirm={() => clear.mutate()}
      />
      <ConfirmDialog
        open={unlinking !== null}
        onOpenChange={(o) => !o && setUnlinking(null)}
        title="Unlink this chat?"
        description={<>Kino will no longer be able to send to {unlinking?.title}.</>}
        confirmLabel="Unlink"
        pending={unlink.isPending}
        onConfirm={() => unlinking && unlink.mutate(unlinking)}
      />
      <LinkChatDialog open={linkOpen} onOpenChange={setLinkOpen} />
    </div>
  );
}
