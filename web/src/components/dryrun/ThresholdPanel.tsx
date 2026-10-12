import { Slider } from "@/components/ui/slider";
import { thresholdAt, withValue } from "@/lib/dryrun";
import { fieldError, type ParsedErrors, type Thresholds } from "@/lib/settings";

const ROWS: { tier: "L" | "M" | "S"; title: string; hint: string; paths: [string, string][] }[] = [
  { tier: "L", title: "Tier L (80 px and up)", hint: "Full trust", paths: [["L.auto", "Auto-save"], ["L.review", "Review"]] },
  { tier: "M", title: "Tier M (40-79 px)", hint: "Stricter", paths: [["M.auto", "Auto-save"], ["M.review", "Review"]] },
  { tier: "S", title: "Tier S (24-39 px)", hint: "Never auto-saved; review only", paths: [["S.review", "Review"]] },
];

function Row({
  path,
  tier,
  label,
  value,
  error,
  onChange,
}: {
  path: string;
  tier: string;
  label: string;
  value: number;
  error?: string;
  onChange: (v: number) => void;
}) {
  const id = `dry-${path.replace(".", "-")}`;
  const name = `Tier ${tier} ${label.toLowerCase()}`;
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-3">
        <label htmlFor={id} className="w-20 shrink-0 text-sm text-muted-foreground">
          {label}
        </label>
        <Slider
          aria-label={`${name} slider`}
          min={0}
          max={1}
          step={0.01}
          value={[Number.isFinite(value) ? Math.min(Math.max(value, 0), 1) : 0]}
          onValueChange={([x]) => x !== undefined && onChange(x)}
        />
        <input
          id={id}
          type="number"
          aria-label={name}
          aria-invalid={error ? true : undefined}
          step={0.01}
          min={0}
          max={1}
          value={Number.isFinite(value) ? value : ""}
          onChange={(e) => onChange(e.target.value === "" ? NaN : Number(e.target.value))}
          className="h-8 w-20 shrink-0 rounded-md border border-input bg-background px-2 text-sm tabular-nums shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
      </div>
      {error && (
        <p role="alert" className="pl-[5.75rem] text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}

export function ThresholdPanel({
  value,
  local,
  server,
  onChange,
}: {
  value: Thresholds;
  local: Record<string, string>;
  server: ParsedErrors | null;
  onChange: (t: Thresholds) => void;
}) {
  return (
    <div className="space-y-4">
      {ROWS.map((r) => (
        <fieldset key={r.tier} className="space-y-2">
          <legend className="text-sm font-medium">
            {r.title} <span className="font-normal text-muted-foreground">· {r.hint}</span>
          </legend>
          {r.paths.map(([path, label]) => (
            <Row
              key={path}
              path={path}
              tier={r.tier}
              label={label}
              value={thresholdAt(value, path)}
              error={local[path] ?? (server ? fieldError(server, path) : undefined)}
              onChange={(v) => onChange(withValue(value, path, v))}
            />
          ))}
        </fieldset>
      ))}
    </div>
  );
}
