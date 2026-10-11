import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { type FormEvent, useEffect, useId, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Slider } from "@/components/ui/slider";
import { toast } from "@/components/ui/sonner";
import { Switch } from "@/components/ui/switch";
import {
  BIRTH_YEAR_MAX,
  BIRTH_YEAR_MIN,
  createBody,
  emptyForm,
  errorsFromApi,
  type FormErrors,
  formatOffset,
  formFromKid,
  groupKeys,
  groupLabel,
  type Kid,
  type KidForm,
  kidKeys,
  kidsApi,
  NAME_MAX,
  OFFSET_MAX,
  OFFSET_MIN,
  OFFSET_STEP,
  SLUG_MAX,
  updateBody,
  validateForm,
} from "@/lib/kids";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The kid to edit; null creates a new one. */
  kid: Kid | null;
}

function FieldError({ id, msg }: { id: string; msg?: string }) {
  if (!msg) return null;
  return (
    <p id={id} className="text-xs text-destructive" role="alert">
      {msg}
    </p>
  );
}

function GroupPicker({ value, onChange }: { value: number[]; onChange: (ids: number[]) => void }) {
  const groups = useQuery({ queryKey: groupKeys.list, queryFn: kidsApi.groups });
  if (groups.isPending) return <Skeleton className="h-20 w-full" />;
  if (groups.isError) {
    return (
      <p className="text-xs text-muted-foreground">
        Could not load groups.{" "}
        <button type="button" className="underline" onClick={() => groups.refetch()}>
          Retry
        </button>
      </p>
    );
  }
  if (groups.data.length === 0) {
    return <p className="text-xs text-muted-foreground">No groups discovered yet. They appear once OpenWA sends a message.</p>;
  }
  const selected = new Set(value);
  const sorted = [...groups.data].sort(
    (a, b) => Number(b.watched) - Number(a.watched) || groupLabel(a).localeCompare(groupLabel(b)),
  );
  return (
    <div className="max-h-44 space-y-0.5 overflow-y-auto rounded-md border p-1">
      {sorted.map((g) => (
        <label
          key={g.id}
          className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-accent"
        >
          <input
            type="checkbox"
            className="h-4 w-4 accent-[hsl(var(--primary))]"
            checked={selected.has(g.id)}
            onChange={(e) => {
              const next = new Set(selected);
              if (e.target.checked) next.add(g.id);
              else next.delete(g.id);
              onChange([...next]);
            }}
          />
          <span dir="auto" className="min-w-0 flex-1 truncate">
            {groupLabel(g)}
          </span>
          {!g.watched && <span className="text-xs text-muted-foreground">not watched</span>}
        </label>
      ))}
    </div>
  );
}

export function KidFormDialog({ open, onOpenChange, kid }: Props) {
  const qc = useQueryClient();
  const ids = useId();
  const [form, setForm] = useState<KidForm>(emptyForm);
  const [errors, setErrors] = useState<FormErrors>({});

  useEffect(() => {
    if (open) {
      setForm(kid ? formFromKid(kid) : emptyForm());
      setErrors({});
    }
  }, [open, kid]);

  const save = useMutation({
    mutationFn: async (f: KidForm) => {
      if (!kid) return kidsApi.create(createBody(f));
      const body = updateBody(f, kid);
      return Object.keys(body).length ? kidsApi.update(kid.id, body) : kid;
    },
    onSuccess: (saved) => {
      void qc.invalidateQueries({ queryKey: kidKeys.all });
      void qc.invalidateQueries({ queryKey: ["groups"] });
      toast.success(kid ? `Saved ${saved.display_name}` : `Added ${saved.display_name}`);
      onOpenChange(false);
    },
    onError: (e) => setErrors(errorsFromApi(e)),
  });

  const set = <K extends keyof KidForm>(key: K, value: KidForm[K]) => {
    setForm((f) => ({ ...f, [key]: value }));
    if (key in errors || errors.form) setErrors((e) => ({ ...e, [key]: undefined, form: undefined }));
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const v = validateForm(form);
    setErrors(v);
    if (Object.keys(v).length === 0) save.mutate(form);
  };

  const f = (name: string) => `${ids}-${name}`;
  const described = (name: keyof FormErrors) => (errors[name] ? `${f(name)}-err` : undefined);

  return (
    <Dialog open={open} onOpenChange={(o) => !save.isPending && onOpenChange(o)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{kid ? "Edit kid" : "Add a kid"}</DialogTitle>
          <DialogDescription>
            {kid
              ? "Changes to the folder name only affect photos saved from now on."
              : "After adding, enroll a few photos so Kino can recognise them."}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4" noValidate>
          <div className="space-y-1.5">
            <Label htmlFor={f("name")}>Display name</Label>
            <Input
              id={f("name")}
              dir="auto"
              autoFocus
              maxLength={NAME_MAX}
              value={form.display_name}
              onChange={(e) => set("display_name", e.target.value)}
              aria-invalid={!!errors.display_name}
              aria-describedby={described("display_name")}
            />
            <FieldError id={`${f("display_name")}-err`} msg={errors.display_name} />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor={f("slug")}>Folder name (slug)</Label>
              <Input
                id={f("slug")}
                dir="ltr"
                className="font-mono"
                maxLength={SLUG_MAX}
                placeholder={kid ? undefined : "auto from name"}
                value={form.slug}
                onChange={(e) => set("slug", e.target.value)}
                aria-invalid={!!errors.slug}
                aria-describedby={described("slug") ?? `${f("slug")}-help`}
              />
              {errors.slug ? (
                <FieldError id={`${f("slug")}-err`} msg={errors.slug} />
              ) : (
                <p id={`${f("slug")}-help`} className="text-xs text-muted-foreground">
                  {kid ? "Lowercase letters, digits and dashes." : "Leave empty to transliterate the name."}
                </p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={f("year")}>Birth year</Label>
              <Input
                id={f("year")}
                type="number"
                inputMode="numeric"
                min={BIRTH_YEAR_MIN}
                max={BIRTH_YEAR_MAX}
                placeholder="optional"
                value={form.birth_year}
                onChange={(e) => set("birth_year", e.target.value)}
                aria-invalid={!!errors.birth_year}
                aria-describedby={described("birth_year")}
              />
              <FieldError id={`${f("birth_year")}-err`} msg={errors.birth_year} />
            </div>
          </div>

          <div className="space-y-1.5">
            <Label id={f("groups")}>Mapped groups</Label>
            <p className="text-xs text-muted-foreground">
              Groups this kid is usually in. Matches there need a slightly lower score.
            </p>
            <div role="group" aria-labelledby={f("groups")}>
              <GroupPicker value={form.group_ids} onChange={(v) => set("group_ids", v)} />
            </div>
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label id={f("offset")}>Threshold offset</Label>
              <span className="text-sm font-medium tabular-nums">{formatOffset(form.threshold_offset)}</span>
            </div>
            <Slider
              aria-labelledby={f("offset")}
              min={OFFSET_MIN}
              max={OFFSET_MAX}
              step={OFFSET_STEP}
              value={[form.threshold_offset]}
              onValueChange={([v]) => set("threshold_offset", v ?? 0)}
            />
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>{formatOffset(OFFSET_MIN)} looser</span>
              <span>stricter {formatOffset(OFFSET_MAX)}</span>
            </div>
          </div>

          <div className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2">
            <div>
              <Label htmlFor={f("active")}>Active</Label>
              <p className="text-xs text-muted-foreground">Inactive kids are not matched.</p>
            </div>
            <Switch id={f("active")} checked={form.active} onCheckedChange={(v) => set("active", v)} />
          </div>

          {errors.form && (
            <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {errors.form}
            </p>
          )}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={save.isPending}>
              Cancel
            </Button>
            <Button type="submit" disabled={save.isPending}>
              {save.isPending && <Loader2 className="animate-spin" />}
              {kid ? "Save" : "Add kid"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
