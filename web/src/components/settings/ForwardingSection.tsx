import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Send } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { toast } from "@/components/ui/sonner";
import { Textarea } from "@/components/ui/textarea";
import { isApiError } from "@/lib/api";
import type { Kid } from "@/lib/kids";
import {
  CAPTION_MAX,
  CAPTION_PLACEHOLDERS,
  type ForwardSettings,
  fieldError,
  isDirty,
  type ParsedErrors,
  parseFieldErrors,
  previewCaption,
  sampleCaption,
  settingsApi,
  settingsKeys,
  type SettingsOut,
  unknownPlaceholders,
} from "@/lib/settings";
import { ChatPicker, useChatNames } from "./ChatPicker";
import { ConfirmDialog, Field, SectionCard } from "./SectionCard";

export function ForwardingSection({ saved, kids }: { saved: ForwardSettings; kids: Kid[] }) {
  const qc = useQueryClient();
  const names = useChatNames();
  const [draft, setDraft] = useState<ForwardSettings | null>(null);
  const [errors, setErrors] = useState<ParsedErrors | null>(null);
  const [confirmTest, setConfirmTest] = useState(false);
  const form = draft ?? saved;
  const set = (patch: Partial<ForwardSettings>) => setDraft({ ...form, ...patch });
  const dirty = isDirty(draft, saved);

  const save = useMutation({
    mutationFn: settingsApi.putForward,
    onSuccess: (out) => {
      qc.setQueryData<SettingsOut>(settingsKeys.all, (s) => (s ? { ...s, forward: out } : s));
      setDraft(null);
      setErrors(null);
      toast.success("Forwarding settings saved");
    },
    onError: (e) => {
      setErrors(parseFieldErrors(e.message));
      toast.error("Could not save forwarding settings", { description: e.message });
    },
  });

  const test = useMutation({
    mutationFn: settingsApi.testForward,
    onSuccess: () => toast.success("Test photo sent"),
    onError: (e) =>
      toast.error(isApiError(e, 429) ? "Please wait before sending another test" : "Could not send the test photo", {
        description: e.message,
      }),
    onSettled: () => setConfirmTest(false),
  });

  const sample = sampleCaption(kids.map((k) => k.display_name));
  const unknown = unknownPlaceholders(form.caption_template);
  const emptyCaption = form.send_caption && form.caption_template.trim() === "";
  const tooLong = form.send_caption && form.caption_template.length > CAPTION_MAX;
  const captionError =
    (errors && fieldError(errors, "caption_template")) ||
    (emptyCaption ? "The caption cannot be empty" : tooLong ? `At most ${CAPTION_MAX} characters` : undefined);
  const testTarget = form.default_chat_id;

  return (
    <SectionCard
      id="forwarding"
      title="Forwarding"
      description="Send every saved photo to a WhatsApp chat or group. Watched groups cannot be destinations."
      dirty={dirty}
      saving={save.isPending}
      canSave={!captionError}
      errors={errors}
      onSave={() => save.mutate(form)}
      onReset={() => {
        setDraft(null);
        setErrors(null);
      }}
    >
      <label className="flex items-center justify-between gap-3">
        <span className="text-sm font-medium">Forward saved photos</span>
        <Switch aria-label="Enable forwarding" checked={form.enabled} onCheckedChange={(v) => set({ enabled: v })} />
      </label>

      <Field
        id="fwd-default"
        label="Default destination"
        error={errors ? fieldError(errors, "default_chat_id") : undefined}
        hint="Used for every kid without an override below."
      >
        <ChatPicker
          id="fwd-default"
          label="Default destination"
          value={form.default_chat_id}
          names={names}
          onChange={(v) => set({ default_chat_id: v })}
        />
      </Field>

      <div className="space-y-2">
        <p className="text-sm font-medium">Per-kid overrides</p>
        {kids.length === 0 ? (
          <p className="text-sm text-muted-foreground">Add a kid to set a per-kid destination.</p>
        ) : (
          <ul className="space-y-2">
            {kids.map((k) => {
              const pathErr = errors ? fieldError(errors, `per_kid.${k.id}`) : undefined;
              return (
                <li key={k.id} className="grid items-center gap-1 sm:grid-cols-[10rem_1fr] sm:gap-3">
                  <span className="truncate text-sm" dir="auto">
                    {k.display_name}
                  </span>
                  <div>
                    <ChatPicker
                      id={`fwd-kid-${k.id}`}
                      label={`Destination for ${k.display_name}`}
                      value={form.per_kid[String(k.id)] ?? null}
                      names={names}
                      emptyLabel="Use default"
                      error={pathErr}
                      onChange={(v) => {
                        const next = { ...form.per_kid };
                        if (v) next[String(k.id)] = v;
                        else delete next[String(k.id)];
                        set({ per_kid: next });
                      }}
                    />
                    {pathErr && (
                      <p id={`fwd-kid-${k.id}-error`} role="alert" className="mt-1 text-xs text-destructive">
                        {pathErr}
                      </p>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <label className="flex items-center justify-between gap-3">
        <span className="text-sm">Send photos with a caption</span>
        <Switch
          aria-label="Send photos with a caption"
          checked={form.send_caption}
          onCheckedChange={(v) => set({ send_caption: v })}
        />
      </label>

      <Field
        id="fwd-caption"
        label="Caption template"
        error={captionError}
        hint={`Placeholders: ${CAPTION_PLACEHOLDERS.map((p) => `{${p}}`).join(" ")}`}
      >
        <Textarea
          id="fwd-caption"
          dir="auto"
          rows={2}
          disabled={!form.send_caption}
          value={form.caption_template}
          aria-invalid={captionError ? true : undefined}
          aria-describedby={captionError ? "fwd-caption-error" : undefined}
          onChange={(e) => set({ caption_template: e.target.value })}
        />
      </Field>
      {unknown.length > 0 && (
        <p className="text-xs text-amber-600 dark:text-amber-400" data-testid="unknown-placeholders">
          Unknown placeholder{unknown.length > 1 ? "s" : ""} {unknown.join(" ")} will be sent as literal text.
        </p>
      )}
      <div className="rounded-md bg-muted px-3 py-2 text-sm">
        <p className="mb-1 text-xs font-medium text-muted-foreground">Preview</p>
        <p dir="auto" data-testid="caption-preview" className="whitespace-pre-wrap break-words">
          {form.send_caption ? previewCaption(form.caption_template, sample) : "(photo is sent without a caption)"}
        </p>
      </div>

      <label className="flex items-center justify-between gap-3">
        <span className="text-sm">Also forward photos confirmed from the review queue</span>
        <Switch
          aria-label="Include review-confirmed photos"
          checked={form.include_review_confirmed}
          onCheckedChange={(v) => set({ include_review_confirmed: v })}
        />
      </label>

      <div>
        <Button variant="outline" disabled={!testTarget || test.isPending} onClick={() => setConfirmTest(true)}>
          <Send /> Send test photo
        </Button>
        {!testTarget && <p className="mt-1 text-xs text-muted-foreground">Pick a default destination to send a test.</p>}
      </div>

      <ConfirmDialog
        open={confirmTest}
        onOpenChange={setConfirmTest}
        title="Send a test photo?"
        description={
          <p>
            A real WhatsApp message with a generated test photo will be sent to{" "}
            <strong dir="auto">{testTarget ? (names.get(testTarget)?.trim() || testTarget) : ""}</strong>.
          </p>
        }
        confirmLabel="Send test photo"
        pending={test.isPending}
        onConfirm={() => testTarget && test.mutate(testTarget)}
      />
    </SectionCard>
  );
}
