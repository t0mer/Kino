import { Check, X } from "lucide-react";
import { ItemMeta } from "@/components/review/ItemMeta";
import { Button } from "@/components/ui/button";
import type { ReviewItem } from "@/lib/review";
import { cn } from "@/lib/utils";

interface Props {
  items: ReviewItem[];
  selected: ReadonlySet<number>;
  busy: boolean;
  onToggle: (id: number, shift: boolean) => void;
  onSelectAll: (all: boolean) => void;
  onAct: (action: "confirm" | "reject") => void;
}

/** Grid view with multi-select for confirming or rejecting many items at once. */
export function BulkGrid({ items, selected, busy, onToggle, onSelectAll, onAct }: Props) {
  const allSelected = items.length > 0 && items.every((i) => selected.has(i.match_id));
  return (
    <div className="space-y-4">
      <div className="sticky top-14 z-10 flex flex-wrap items-center gap-3 rounded-lg border bg-background/90 p-3 backdrop-blur lg:top-0">
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={allSelected}
            onChange={(e) => onSelectAll(e.target.checked)}
            aria-label="Select all"
            className="h-4 w-4 accent-[hsl(var(--primary))]"
          />
          Select all
        </label>
        <span className="text-sm text-muted-foreground" aria-live="polite">
          {selected.size} selected
        </span>
        <div className="ms-auto flex gap-2">
          <Button size="sm" disabled={busy || selected.size === 0} onClick={() => onAct("confirm")}>
            <Check /> Confirm selected
          </Button>
          <Button size="sm" variant="outline" disabled={busy || selected.size === 0} onClick={() => onAct("reject")}>
            <X /> Reject selected
          </Button>
        </div>
      </div>
      <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
        {items.map((i) => {
          const on = selected.has(i.match_id);
          return (
            <li
              key={i.match_id}
              data-testid={`bulk-item-${i.match_id}`}
              className={cn("rounded-lg border bg-card p-2 transition-colors", on && "border-primary bg-accent/50")}
            >
              <label className="block cursor-pointer space-y-2">
                <div className="relative aspect-square overflow-hidden rounded-md bg-muted">
                  {i.crop_url && <img src={i.crop_url} alt="" className="h-full w-full object-cover" />}
                  <input
                    type="checkbox"
                    checked={on}
                    onChange={() => undefined}
                    onClick={(e) => onToggle(i.match_id, e.shiftKey)}
                    aria-label={`Select match ${i.match_id}`}
                    className="absolute start-2 top-2 h-5 w-5 accent-[hsl(var(--primary))]"
                  />
                </div>
                <p className="truncate text-sm font-medium" dir="auto">
                  {i.kid_name}
                </p>
                <ItemMeta item={i} />
              </label>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
