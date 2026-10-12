import { useMutation, useQueryClient } from "@tanstack/react-query";
import { RotateCcw } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { toast } from "@/components/ui/sonner";
import {
  DEFAULT_THRESHOLDS,
  fieldError,
  isDirty,
  type ParsedErrors,
  parseFieldErrors,
  settingsApi,
  settingsKeys,
  type SettingsOut,
  toThresholds,
  type Thresholds,
  type ThresholdsOut,
  validateThresholds,
} from "@/lib/settings";
import { Field, NumberField, SectionCard } from "./SectionCard";

type Path = "L.auto" | "L.review" | "M.auto" | "M.review" | "S.review";

function getAt(t: Thresholds, path: Path): number {
  const [tier, key] = path.split(".") as ["L" | "M" | "S", string];
  return (t[tier] as unknown as Record<string, number>)[key] ?? NaN;
}

function setAt(t: Thresholds, path: Path, v: number): Thresholds {
  const [tier, key] = path.split(".") as ["L" | "M" | "S", string];
  return { ...t, [tier]: { ...t[tier], [key]: v } };
}

function ScoreField({
  path,
  label,
  form,
  onChange,
  error,
}: {
  path: Path;
  label: string;
  form: Thresholds;
  onChange: (v: number) => void;
  error?: string;
}) {
  const v = getAt(form, path);
  const id = `th-${path.replace(".", "-")}`;
  return (
    <Field id={id} label={label} error={error}>
      <div className="flex items-center gap-3">
        <Slider
          aria-label={`${label} slider`}
          min={0}
          max={1}
          step={0.01}
          value={[Number.isFinite(v) ? Math.min(Math.max(v, 0), 1) : 0]}
          onValueChange={([x]) => x !== undefined && onChange(Math.round(x * 100) / 100)}
        />
        <NumberField id={id} className="w-24 shrink-0" value={v} step={0.01} min={-1} max={1} onChange={onChange} error={error} />
      </div>
    </Field>
  );
}

export function ThresholdsSection({ saved }: { saved: ThresholdsOut }) {
  const qc = useQueryClient();
  const savedForm = toThresholds(saved);
  const [draft, setDraft] = useState<Thresholds | null>(null);
  const [serverErrors, setServerErrors] = useState<ParsedErrors | null>(null);
  const form = draft ?? savedForm;
  const local = validateThresholds(form, saved.auto_floor);
  const err = (path: string) => local[path] ?? (serverErrors ? fieldError(serverErrors, path) : undefined);
  const edit = (next: Thresholds) => {
    setDraft(next);
    setServerErrors(null);
  };

  const save = useMutation({
    mutationFn: settingsApi.putThresholds,
    onSuccess: (out) => {
      qc.setQueryData<SettingsOut>(settingsKeys.all, (s) => (s ? { ...s, thresholds: out } : s));
      setDraft(null);
      setServerErrors(null);
      toast.success("Thresholds saved");
    },
    onError: (e) => {
      setServerErrors(parseFieldErrors(e.message));
      toast.error("Could not save thresholds", { description: e.message });
    },
  });

  const tiers: { tier: "L" | "M"; title: string }[] = [
    { tier: "L", title: "Tier L (faces 80 px and larger)" },
    { tier: "M", title: "Tier M (40 to 79 px)" },
  ];

  return (
    <SectionCard
      id="thresholds"
      title="Thresholds"
      description={`Match scores needed to auto-save or send to review. Auto-save never happens below ${saved.auto_floor.toFixed(2)}.`}
      dirty={isDirty(draft, savedForm)}
      saving={save.isPending}
      canSave={Object.keys(local).length === 0}
      errors={serverErrors}
      onSave={() => save.mutate(form)}
      onReset={() => {
        setDraft(null);
        setServerErrors(null);
      }}
    >
      {tiers.map(({ tier, title }) => (
        <fieldset key={tier} className="space-y-3 rounded-lg border p-3">
          <legend className="px-1 text-sm font-medium">{title}</legend>
          <div className="grid gap-3 sm:grid-cols-2">
            <ScoreField path={`${tier}.auto`} label={`${tier} auto-save`} form={form} error={err(`${tier}.auto`) ?? err(tier)} onChange={(v) => edit(setAt(form, `${tier}.auto`, v))} />
            <ScoreField path={`${tier}.review`} label={`${tier} review`} form={form} error={err(`${tier}.review`)} onChange={(v) => edit(setAt(form, `${tier}.review`, v))} />
          </div>
        </fieldset>
      ))}
      <fieldset className="space-y-3 rounded-lg border p-3">
        <legend className="px-1 text-sm font-medium">Tier S (24 to 39 px, never auto-saved)</legend>
        <ScoreField path="S.review" label="S review" form={form} error={err("S.review")} onChange={(v) => edit(setAt(form, "S.review", v))} />
      </fieldset>
      <fieldset className="space-y-3 rounded-lg border p-3">
        <legend className="px-1 text-sm font-medium">Matching adjustments</legend>
        <div className="grid gap-3 sm:grid-cols-3">
          {(
            [
              ["prior_bonus", "Group prior bonus"],
              ["unmapped_penalty", "Unmapped group penalty"],
              ["margin_min", "Minimum margin"],
            ] as const
          ).map(([key, label]) => (
            <Field key={key} id={`th-${key}`} label={label} error={err(key)}>
              <NumberField id={`th-${key}`} value={form[key]} step={0.01} min={-1} max={1} error={err(key)} onChange={(v) => edit({ ...form, [key]: v })} />
            </Field>
          ))}
        </div>
      </fieldset>
      <Button variant="outline" size="sm" onClick={() => edit({ ...DEFAULT_THRESHOLDS })}>
        <RotateCcw /> Reset to defaults
      </Button>
    </SectionCard>
  );
}
