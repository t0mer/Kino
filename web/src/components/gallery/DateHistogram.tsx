import { type Histogram, monthLabel } from "@/lib/gallery";

/** Pure-CSS bar chart of reference photo dates by month, so age drift is visible at a glance. */
export function DateHistogram({ data }: { data: Histogram }) {
  if (data.buckets.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        {data.undated > 0 ? `${data.undated} reference${data.undated === 1 ? " has" : "s have"} no photo date.` : "No dated references."}
      </p>
    );
  }
  const first = data.buckets[0]!;
  const last = data.buckets[data.buckets.length - 1]!;
  return (
    <figure className="space-y-1" aria-label="References by photo month">
      <div className="flex h-16 items-end gap-px" role="img" aria-label={`Photo dates from ${monthLabel(first.key)} to ${monthLabel(last.key)}`}>
        {data.buckets.map((b) => (
          <div
            key={b.key}
            title={`${monthLabel(b.key)}: ${b.count}`}
            data-testid="hist-bar"
            data-month={b.key}
            className={b.count > 0 ? "min-w-0.5 flex-1 rounded-t-sm bg-primary" : "min-w-0.5 flex-1 rounded-t-sm bg-muted"}
            style={{ height: b.count > 0 ? `${Math.max(8, (b.count / data.max) * 100)}%` : "2px" }}
          />
        ))}
      </div>
      <figcaption className="flex justify-between text-xs text-muted-foreground">
        <span>{monthLabel(first.key)}</span>
        {data.undated > 0 && <span>{data.undated} undated</span>}
        <span>{monthLabel(last.key)}</span>
      </figcaption>
    </figure>
  );
}
