import { Loader2 } from "lucide-react";
import { type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { ParsedErrors } from "@/lib/settings";

/** A settings section with its own Save / Reset footer. */
export function SectionCard({
  id,
  title,
  description,
  dirty,
  saving,
  canSave = true,
  onSave,
  onReset,
  errors,
  children,
}: {
  id: string;
  title: string;
  description?: ReactNode;
  dirty: boolean;
  saving: boolean;
  canSave?: boolean;
  onSave: () => void;
  onReset: () => void;
  errors?: ParsedErrors | null;
  children: ReactNode;
}) {
  return (
    <Card aria-labelledby={`${id}-title`} role="region" data-testid={`section-${id}`}>
      <CardContent className="space-y-4 p-5">
        <div>
          <h2 id={`${id}-title`} className="text-lg font-semibold">
            {title}
          </h2>
          {description && <div className="text-sm text-muted-foreground">{description}</div>}
        </div>
        {errors && errors.general.length > 0 && (
          <p role="alert" className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {errors.general.join("; ")}
          </p>
        )}
        {children}
        <div className="flex flex-wrap items-center gap-2 border-t pt-4">
          <Button onClick={onSave} disabled={!dirty || saving || !canSave} aria-label={`Save ${title}`}>
            {saving && <Loader2 className="animate-spin" />} Save
          </Button>
          <Button variant="ghost" onClick={onReset} disabled={!dirty || saving}>
            Discard changes
          </Button>
          {dirty && <span className="text-xs text-muted-foreground">Unsaved changes</span>}
        </div>
      </CardContent>
    </Card>
  );
}

export function Field({
  id,
  label,
  hint,
  error,
  children,
}: {
  id: string;
  label: string;
  hint?: ReactNode;
  error?: string;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint && !error && <p className="text-xs text-muted-foreground">{hint}</p>}
      {error && (
        <p id={`${id}-error`} role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}

/** Numeric input that keeps NaN for an empty box so validation can flag it. */
export function NumberField({
  id,
  value,
  onChange,
  error,
  min,
  max,
  step,
  label,
  className,
}: {
  id: string;
  value: number;
  onChange: (v: number) => void;
  error?: string;
  min?: number;
  max?: number;
  step?: number;
  label?: string;
  className?: string;
}) {
  return (
    <Input
      id={id}
      type="number"
      inputMode="decimal"
      aria-label={label}
      aria-invalid={error ? true : undefined}
      aria-describedby={error ? `${id}-error` : undefined}
      className={className}
      min={min}
      max={max}
      step={step}
      value={Number.isNaN(value) ? "" : value}
      onChange={(e) => onChange(e.target.value === "" ? NaN : Number(e.target.value))}
    />
  );
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  pending,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  title: string;
  description: ReactNode;
  confirmLabel: string;
  pending: boolean;
  onConfirm: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription asChild>
            <div>{description}</div>
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={pending}>
            Cancel
          </Button>
          <Button onClick={onConfirm} disabled={pending}>
            {pending && <Loader2 className="animate-spin" />} {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
