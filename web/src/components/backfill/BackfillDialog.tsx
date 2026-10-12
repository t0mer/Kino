import { Loader2 } from "lucide-react";
import { type FormEvent, useState } from "react";
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
import { DEFAULT_DAYS, MAX_DAYS, MIN_DAYS, parseDays } from "@/lib/backfill";

export function BackfillDialog({
  groupName,
  starting,
  onStart,
  onClose,
}: {
  groupName: string;
  starting: boolean;
  /** Resolves true when a job is tracked; the dialog then closes. */
  onStart: (days: number) => Promise<boolean>;
  onClose: () => void;
}) {
  const [raw, setRaw] = useState(String(DEFAULT_DAYS));
  const days = parseDays(raw);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (days === null) return;
    if (await onStart(days)) onClose();
  }

  return (
    <Dialog open onOpenChange={(o) => !o && !starting && onClose()}>
      <DialogContent>
        <form onSubmit={submit} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>
              Backfill <span dir="auto">{groupName}</span>
            </DialogTitle>
            <DialogDescription>
              Fetches the photos posted in this group over the last N days and runs them through Kino. It is
              rate-limited, so it may take several minutes. Photos already seen are skipped.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-1.5">
            <Label htmlFor="backfill-days">Days to fetch</Label>
            <Input
              id="backfill-days"
              inputMode="numeric"
              value={raw}
              onChange={(e) => setRaw(e.target.value)}
              aria-invalid={days === null}
              aria-describedby="backfill-days-hint"
              className="w-28"
            />
            <p id="backfill-days-hint" className={days === null ? "text-sm text-destructive" : "text-sm text-muted-foreground"}>
              A whole number from {MIN_DAYS} to {MAX_DAYS}.
            </p>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={starting}>
              Cancel
            </Button>
            <Button type="submit" disabled={days === null || starting}>
              {starting && <Loader2 className="animate-spin" />} Start backfill
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
