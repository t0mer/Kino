import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "@/components/ui/sonner";
import {
  fieldError,
  isDirty,
  type ParsedErrors,
  parseFieldErrors,
  type Retention,
  settingsApi,
  settingsKeys,
  type SettingsOut,
  validateRetention,
} from "@/lib/settings";
import { Field, NumberField, SectionCard } from "./SectionCard";

export function RetentionSection({ saved }: { saved: Retention }) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<Retention | null>(null);
  const [serverErrors, setServerErrors] = useState<ParsedErrors | null>(null);
  const form = draft ?? saved;
  const local = validateRetention(form);
  const err = (k: "raw_days" | "face_days") => local[k] ?? (serverErrors ? fieldError(serverErrors, k) : undefined);

  const save = useMutation({
    mutationFn: settingsApi.putRetention,
    onSuccess: (out) => {
      qc.setQueryData<SettingsOut>(settingsKeys.all, (s) => (s ? { ...s, retention: out } : s));
      setDraft(null);
      setServerErrors(null);
      toast.success("Retention saved");
    },
    onError: (e) => {
      setServerErrors(parseFieldErrors(e.message));
      toast.error("Could not save retention", { description: e.message });
    },
  });

  return (
    <SectionCard
      id="retention"
      title="Retention"
      description="How long data about people who are not your kids is kept."
      dirty={isDirty(draft, saved)}
      saving={save.isPending}
      canSave={Object.keys(local).length === 0}
      errors={serverErrors}
      onSave={() => save.mutate(form)}
      onReset={() => {
        setDraft(null);
        setServerErrors(null);
      }}
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field id="ret-raw" label="Unmatched raw photos (days)" error={err("raw_days")} hint="Photos that matched no kid are deleted after this many days.">
          <NumberField id="ret-raw" value={form.raw_days} min={1} max={3650} step={1} error={err("raw_days")} onChange={(v) => { setDraft({ ...form, raw_days: v }); setServerErrors(null); }} />
        </Field>
        <Field id="ret-face" label="Unmatched faces (days)" error={err("face_days")} hint="Embeddings and crops of non-kid faces are deleted after this many days.">
          <NumberField id="ret-face" value={form.face_days} min={1} max={3650} step={1} error={err("face_days")} onChange={(v) => { setDraft({ ...form, face_days: v }); setServerErrors(null); }} />
        </Field>
      </div>
    </SectionCard>
  );
}
