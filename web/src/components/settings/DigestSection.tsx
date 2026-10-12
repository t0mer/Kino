import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Info, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toast } from "@/components/ui/sonner";
import {
  type DigestForm,
  type DigestOut,
  digestPayload,
  digestToForm,
  fieldError,
  isDirty,
  type ParsedErrors,
  parseFieldErrors,
  settingsApi,
  settingsKeys,
  type SettingsOut,
} from "@/lib/settings";
import { ChatPicker, useChatNames } from "./ChatPicker";
import { Field, SectionCard } from "./SectionCard";

export function DigestSection({ saved }: { saved: DigestOut }) {
  const qc = useQueryClient();
  const names = useChatNames();
  const savedForm = digestToForm(saved);
  const [draft, setDraft] = useState<DigestForm | null>(null);
  const [errors, setErrors] = useState<ParsedErrors | null>(null);
  const form = draft ?? savedForm;
  const set = (patch: Partial<DigestForm>) => setDraft({ ...form, ...patch });
  const err = (path: string) => (errors ? fieldError(errors, path) : undefined);

  const save = useMutation({
    mutationFn: settingsApi.putDigest,
    onSuccess: (out) => {
      qc.setQueryData<SettingsOut>(settingsKeys.all, (s) => (s ? { ...s, digest: out } : s));
      setDraft(null);
      setErrors(null);
      toast.success("Digest settings saved");
    },
    onError: (e) => {
      setErrors(parseFieldErrors(e.message));
      toast.error("Could not save digest settings", { description: e.message });
    },
  });

  return (
    <SectionCard
      id="digest"
      title="Review digest"
      description={
        <>
          A daily message when photos wait in the review queue.{" "}
          <span className="inline-flex items-center gap-1" data-testid="digest-note">
            <Info className="h-3.5 w-3.5" aria-hidden="true" /> A digest is sent at this time only when new photos are waiting for review.
          </span>
        </>
      }
      dirty={isDirty(draft, savedForm)}
      saving={save.isPending}
      errors={errors}
      onSave={() => save.mutate(digestPayload(form))}
      onReset={() => {
        setDraft(null);
        setErrors(null);
      }}
    >
      <Field id="digest-chat" label="Digest chat" error={err("chat_id")}>
        <ChatPicker id="digest-chat" label="Digest chat" value={form.chat_id} names={names} onChange={(v) => set({ chat_id: v })} />
      </Field>
      <Field
        id="digest-cron"
        label="Check time (cron)"
        error={err("cron")}
        hint="Five fields: minute hour day month weekday, e.g. 0 20 * * * for 20:00 daily. Empty disables the digest."
      >
        <Input
          id="digest-cron"
          value={form.cron}
          placeholder="0 20 * * *"
          autoComplete="off"
          aria-invalid={err("cron") ? true : undefined}
          aria-describedby={err("cron") ? "digest-cron-error" : undefined}
          onChange={(e) => set({ cron: e.target.value })}
        />
      </Field>
      <Field
        id="digest-base"
        label="Public base URL"
        error={err("public_base_url")}
        hint="Used for the review link in the message, e.g. https://kino.example.com"
      >
        <Input
          id="digest-base"
          type="url"
          value={form.public_base_url}
          placeholder="https://kino.example.com"
          aria-invalid={err("public_base_url") ? true : undefined}
          aria-describedby={err("public_base_url") ? "digest-base-error" : undefined}
          onChange={(e) => set({ public_base_url: e.target.value })}
        />
      </Field>
      <div className="space-y-2">
        <p className="text-sm font-medium">Extra notification URLs (Apprise)</p>
        <p className="text-xs text-muted-foreground">
          Stored URLs are hidden. Keep an entry as it is, or type a full URL to replace it.
        </p>
        {err("apprise_urls") && (
          <p role="alert" className="text-xs text-destructive">
            {err("apprise_urls")}
          </p>
        )}
        <ul className="space-y-2">
          {form.apprise_urls.map((u, i) => (
            <li key={i} className="flex items-center gap-2">
              <Input
                aria-label={`Notification URL ${i + 1}`}
                value={u}
                autoComplete="off"
                placeholder="tgram://token/chat"
                onChange={(e) => set({ apprise_urls: form.apprise_urls.map((x, j) => (j === i ? e.target.value : x)) })}
              />
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Remove notification URL ${i + 1}`}
                onClick={() => set({ apprise_urls: form.apprise_urls.filter((_, j) => j !== i) })}
              >
                <Trash2 />
              </Button>
            </li>
          ))}
        </ul>
        <Button variant="outline" size="sm" onClick={() => set({ apprise_urls: [...form.apprise_urls, ""] })}>
          <Plus /> Add URL
        </Button>
      </div>
    </SectionCard>
  );
}
