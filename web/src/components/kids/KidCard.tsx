import { useQuery } from "@tanstack/react-query";
import { Lightbulb, Pencil, ScanFace, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  bestReference,
  coveragePercent,
  formatOffset,
  initials,
  type Kid,
  kidKeys,
  kidsApi,
} from "@/lib/kids";
import { cn } from "@/lib/utils";

export function KidAvatar({ kid }: { kid: Kid }) {
  const refs = useQuery({
    queryKey: kidKeys.avatar(kid.id),
    queryFn: () => kidsApi.activeReferences(kid.id),
    enabled: kid.reference_count > 0,
    staleTime: 5 * 60_000,
  });
  const [broken, setBroken] = useState(false);
  const best = refs.data ? bestReference(refs.data) : null;
  const box = "h-16 w-16 shrink-0 overflow-hidden rounded-full border bg-accent text-accent-foreground";
  if (best?.crop_url && !broken) {
    return (
      <div className={box}>
        <img
          src={best.crop_url}
          alt={`${kid.display_name} reference face`}
          className="h-full w-full object-cover"
          onError={() => setBroken(true)}
        />
      </div>
    );
  }
  return (
    <div className={cn(box, "flex items-center justify-center text-xl font-semibold")} aria-hidden="true">
      <span dir="auto">{initials(kid.display_name)}</span>
    </div>
  );
}

export function CoverageMeter({ kid }: { kid: Kid }) {
  const cov = useQuery({
    queryKey: kidKeys.coverage(kid.id),
    queryFn: () => kidsApi.coverage(kid.id),
  });
  if (cov.isPending) return <Skeleton className="h-10 w-full" />;
  if (cov.isError) {
    return (
      <p className="text-xs text-muted-foreground">
        Coverage unavailable.{" "}
        <button type="button" className="underline" onClick={() => cov.refetch()}>
          Retry
        </button>
      </p>
    );
  }
  const c = cov.data;
  const pct = coveragePercent(c.count, c.recommended);
  const enough = c.count >= c.recommended;
  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between text-xs">
        <span className="font-medium">Coverage</span>
        <span className="tabular-nums text-muted-foreground">
          {c.count} / {c.recommended} references
        </span>
      </div>
      <div
        role="progressbar"
        aria-label={`Coverage for ${kid.display_name}`}
        aria-valuemin={0}
        aria-valuemax={c.recommended}
        aria-valuenow={Math.min(c.count, c.recommended)}
        className="h-2 w-full overflow-hidden rounded-full bg-secondary"
      >
        <div
          className={cn("h-full rounded-full transition-all", enough ? "bg-success" : "bg-primary")}
          style={{ width: `${pct}%` }}
        />
      </div>
      {c.yaw_known > 0 && (
        <p className="text-xs text-muted-foreground tabular-nums">
          Angles: {c.left} left · {c.frontal} frontal · {c.right} right
        </p>
      )}
      {c.hints.length > 0 && (
        <ul className="space-y-1" aria-label="Coverage hints">
          {c.hints.map((h) => (
            <li key={h} className="flex items-start gap-1.5 text-xs text-accent-foreground">
              <Lightbulb className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              <span>{h}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

interface KidCardProps {
  kid: Kid;
  onEdit: (kid: Kid) => void;
  onDelete: (kid: Kid) => void;
  onToggleAutoSave: (kid: Kid, enabled: boolean) => void;
  toggling: boolean;
}

export function KidCard({ kid, onEdit, onDelete, onToggleAutoSave, toggling }: KidCardProps) {
  const switchId = `autosave-${kid.id}`;
  return (
    <Card className="flex flex-col" data-testid={`kid-card-${kid.id}`}>
      <CardContent className="flex flex-1 flex-col gap-4 pt-6">
        <div className="flex items-center gap-4">
          <KidAvatar kid={kid} />
          <div className="min-w-0 flex-1 space-y-1">
            <h2 className="truncate text-lg font-semibold" title={kid.display_name}>
              <span dir="auto">{kid.display_name}</span>
            </h2>
            <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
              <span className="font-mono">{kid.slug}</span>
              {kid.birth_year !== null && <span>· born {kid.birth_year}</span>}
              {!kid.active && <Badge variant="outline">Inactive</Badge>}
              {kid.threshold_offset !== 0 && (
                <Badge variant="secondary" title="Threshold offset">
                  {formatOffset(kid.threshold_offset)}
                </Badge>
              )}
            </div>
          </div>
        </div>

        <dl className="grid grid-cols-3 gap-2 text-center">
          {[
            ["References", kid.reference_count],
            ["Today", kid.today],
            ["7 days", kid.last_7_days],
          ].map(([label, value]) => (
            <div key={label} className="rounded-lg bg-muted px-2 py-2">
              <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</dt>
              <dd className="text-lg font-semibold tabular-nums">{value}</dd>
            </div>
          ))}
        </dl>

        <CoverageMeter kid={kid} />

        <div className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2">
          <div>
            <label htmlFor={switchId} className="text-sm font-medium">
              Auto-save
            </label>
            <p className="text-xs text-muted-foreground">
              {kid.auto_save_enabled ? "Confident matches are saved." : "Matches go to review. Check the Test page first."}
            </p>
          </div>
          <Switch
            id={switchId}
            checked={kid.auto_save_enabled}
            disabled={toggling}
            onCheckedChange={(v) => onToggleAutoSave(kid, v)}
          />
        </div>
      </CardContent>
      <CardFooter className="flex flex-wrap gap-2">
        <Button asChild size="sm" className="flex-1">
          <Link to={`/kids/${kid.id}/enroll`}>
            <ScanFace /> Enroll faces
          </Link>
        </Button>
        <Button size="sm" variant="outline" onClick={() => onEdit(kid)} aria-label={`Edit ${kid.display_name}`}>
          <Pencil /> Edit
        </Button>
        <Button
          size="icon"
          variant="ghost"
          className="h-8 w-8 text-destructive hover:text-destructive"
          onClick={() => onDelete(kid)}
          aria-label={`Delete ${kid.display_name}`}
        >
          <Trash2 />
        </Button>
      </CardFooter>
    </Card>
  );
}
