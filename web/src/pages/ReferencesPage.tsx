import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Ban, CalendarDays, FolderInput, Images, Loader2, PlusCircle, RefreshCw, Trash2, Undo2, UserX } from "lucide-react";
import { type ReactNode, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { DateHistogram } from "@/components/references/DateHistogram";
import { CoverageMeter } from "@/components/kids/KidCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/sonner";
import { formatDate } from "@/lib/format";
import {
  countOutliers,
  dateOnly,
  EMPTY_REF_FILTERS,
  filterByDate,
  type ReferenceItem,
  referencesApi,
  referencesKeys,
  hasRefFilters,
  invalidRefRange,
  monthHistogram,
  onlyOutliers,
  outlierReasonText,
  type RefFilters,
  SOURCES,
  sortRefs,
  STATUSES,
} from "@/lib/references";
import { kidKeys, kidsApi } from "@/lib/kids";
import { useLive } from "@/lib/query";
import { usePageTitle } from "@/lib/usePageTitle";
import { PageHeader } from "./PlaceholderPage";

const selectClass =
  "h-9 w-full rounded-md border border-input bg-background px-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex min-w-36 flex-1 flex-col gap-1 text-xs font-medium text-muted-foreground">
      {label}
      {children}
    </label>
  );
}

type Action =
  | { type: "delete"; ref: ReferenceItem }
  | { type: "negative"; ref: ReferenceItem }
  | { type: "move"; ref: ReferenceItem }
  | { type: "date"; ref: ReferenceItem };

export function ReferencesPage() {
  usePageTitle("References");
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [filters, setFilters] = useState<RefFilters>(EMPTY_REF_FILTERS);
  const [outliersOnly, setOutliersOnly] = useState(false);
  const [action, setAction] = useState<Action | null>(null);
  const [moveTo, setMoveTo] = useState("");
  const [dateValue, setDateValue] = useState("");

  const kids = useLive(kidKeys.list, kidsApi.list);
  const kidParam = Number(params.get("kid"));
  const kidId = kids.data?.some((k) => k.id === kidParam) ? kidParam : (kids.data?.[0]?.id ?? null);
  const kid = kids.data?.find((k) => k.id === kidId) ?? null;

  const refs = useQuery({
    queryKey: referencesKeys.refs(kidId ?? 0, filters.source, filters.status),
    queryFn: () => referencesApi.list(kidId!, filters.source, filters.status),
    enabled: kidId !== null,
  });
  const bad = invalidRefRange(filters);
  const outlierCount = useMemo(() => countOutliers(refs.data ?? []), [refs.data]);
  const showOutliersOnly = outliersOnly && outlierCount > 0;
  const visible = useMemo(() => {
    if (bad) return [];
    const dated = filterByDate(refs.data ?? [], filters.from, filters.to);
    return sortRefs(showOutliersOnly ? onlyOutliers(dated) : dated);
  }, [refs.data, filters.from, filters.to, bad, showOutliersOnly]);
  const histogram = useMemo(() => monthHistogram(refs.data ?? []), [refs.data]);

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["kids"] });
  };
  const done = () => {
    setAction(null);
    refresh();
  };
  const failed = (title: string) => (e: Error) => {
    toast.error(title, { description: e.message });
    setAction(null);
    refresh();
  };

  const retire = useMutation({
    mutationFn: (r: ReferenceItem) => referencesApi.setStatus(r.id, r.status === "active" ? "retired" : "active"),
    onSuccess: (r) => {
      toast.success(r.status === "retired" ? "Reference retired" : "Reference reactivated");
      refresh();
    },
    onError: failed("Could not change the reference status"),
  });
  const remove = useMutation({
    mutationFn: (r: ReferenceItem) => referencesApi.remove(r.id),
    onSuccess: () => {
      toast.success("Reference deleted");
      done();
    },
    onError: failed("Could not delete the reference"),
  });
  const negative = useMutation({
    mutationFn: (r: ReferenceItem) => referencesApi.toNegative(r.id),
    onSuccess: () => {
      toast.success("Marked as someone else");
      done();
    },
    onError: failed("Could not mark the reference as negative"),
  });
  const move = useMutation({
    mutationFn: ({ r, to }: { r: ReferenceItem; to: number }) => referencesApi.move(r.id, to),
    onSuccess: () => {
      toast.success("Reference moved");
      done();
    },
    onError: failed("Could not move the reference"),
  });
  const setDate = useMutation({
    mutationFn: ({ r, date }: { r: ReferenceItem; date: string | null }) => referencesApi.setDate(r.id, date),
    onSuccess: () => {
      toast.success("Photo date updated");
      done();
    },
    onError: failed("Could not update the photo date"),
  });

  const busy = remove.isPending || negative.isPending || move.isPending || setDate.isPending;
  const close = () => !busy && setAction(null);
  const set = (patch: Partial<RefFilters>) => setFilters((f) => ({ ...f, ...patch }));
  const selectKid = (id: string) => {
    setParams({ kid: id }, { replace: true });
    setFilters(EMPTY_REF_FILTERS);
    setOutliersOnly(false);
  };
  const open = (a: Action) => {
    setMoveTo("");
    setDateValue(a.type === "date" ? dateOnly(a.ref.photo_date) : "");
    setAction(a);
  };
  const otherKids = kids.data?.filter((k) => k.id !== kidId) ?? [];

  let body: ReactNode;
  if (refs.isPending && kidId !== null) {
    body = (
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5" aria-busy="true" aria-label="Loading references">
        {Array.from({ length: 10 }, (_, i) => (
          <Skeleton key={i} className="h-56 rounded-lg" />
        ))}
      </div>
    );
  } else if (refs.isError) {
    body = (
      <Card>
        <CardContent className="flex flex-col items-center gap-3 py-12 text-center">
          <p className="font-medium">Could not load references</p>
          <p className="text-sm text-muted-foreground">{refs.error.message}</p>
          <Button variant="outline" onClick={() => refs.refetch()}>
            <RefreshCw /> Retry
          </Button>
        </CardContent>
      </Card>
    );
  } else if (bad) {
    body = <p className="py-12 text-center text-sm text-muted-foreground">The "from" date is after the "to" date.</p>;
  } else if (visible.length === 0) {
    const filtered = hasRefFilters(filters);
    body = (
      <Card className="border-dashed">
        <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-accent text-accent-foreground">
            <Images className="h-6 w-6" aria-hidden="true" />
          </div>
          <p className="font-medium">{filtered ? "No references match these filters" : "No references yet"}</p>
          <p className="max-w-sm text-sm text-muted-foreground">
            {filtered
              ? "Try widening the date range or clearing a filter."
              : "Add a few clear photos of this kid so Kino can recognise them."}
          </p>
          {filtered ? (
            <Button variant="outline" onClick={() => setFilters(EMPTY_REF_FILTERS)}>
              Clear filters
            </Button>
          ) : (
            kid && (
              <Button asChild>
                <Link to={`/kids/${kid.id}/enroll`}>
                  <PlusCircle /> Add references
                </Link>
              </Button>
            )
          )}
        </CardContent>
      </Card>
    );
  } else {
    body = (
      <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {visible.map((r) => (
          <li key={r.id}>
            <Card className={r.status === "retired" ? "overflow-hidden opacity-70" : "overflow-hidden"} data-testid={`ref-${r.id}`}>
              <div className="relative aspect-square bg-muted">
                {r.crop_url ? (
                  <img src={r.crop_url} alt={`Reference ${r.id}`} loading="lazy" className="h-full w-full object-cover" />
                ) : (
                  <div className="flex h-full items-center justify-center text-xs text-muted-foreground">No crop</div>
                )}
                <div className="absolute start-1 top-1 flex flex-wrap gap-1">
                  <Badge variant="secondary">{r.source}</Badge>
                  {r.status === "retired" && <Badge variant="outline" className="bg-background">retired</Badge>}
                  {r.outlier && (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <button type="button" className="rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label={`Outlier: ${outlierReasonText(r.outlier_reason)}`}>
                          <Badge variant="outline" className="gap-1 border-destructive bg-background text-destructive">
                            <AlertTriangle className="h-3 w-3" aria-hidden="true" /> Outlier
                          </Badge>
                        </button>
                      </TooltipTrigger>
                      <TooltipContent>{outlierReasonText(r.outlier_reason)}</TooltipContent>
                    </Tooltip>
                  )}
                </div>
              </div>
              <CardContent className="space-y-2 p-2">
                <dl className="grid grid-cols-2 gap-x-2 text-xs tabular-nums">
                  <dt className="text-muted-foreground">Quality</dt>
                  <dd>{r.quality === null ? "—" : r.quality.toFixed(2)}</dd>
                  <dt className="text-muted-foreground">Weight</dt>
                  <dd>{r.weight.toFixed(2)}</dd>
                  <dt className="text-muted-foreground">Photo date</dt>
                  <dd>{r.photo_date ? formatDate(r.photo_date) : "—"}</dd>
                </dl>
                <div className="flex flex-wrap gap-1" role="group" aria-label={`Actions for reference ${r.id}`}>
                  <Button
                    size="icon"
                    variant="outline"
                    title={r.status === "active" ? "Retire" : "Reactivate"}
                    aria-label={`${r.status === "active" ? "Retire" : "Reactivate"} reference ${r.id}`}
                    disabled={retire.isPending}
                    onClick={() => retire.mutate(r)}
                  >
                    {r.status === "active" ? <Ban /> : <Undo2 />}
                  </Button>
                  <Button size="icon" variant="outline" title="Change photo date" aria-label={`Edit date of reference ${r.id}`} onClick={() => open({ type: "date", ref: r })}>
                    <CalendarDays />
                  </Button>
                  <Button size="icon" variant="outline" title="Move to another kid" aria-label={`Move reference ${r.id}`} disabled={otherKids.length === 0} onClick={() => open({ type: "move", ref: r })}>
                    <FolderInput />
                  </Button>
                  <Button size="icon" variant="outline" title="Mark as someone else" aria-label={`Mark reference ${r.id} as negative`} onClick={() => open({ type: "negative", ref: r })}>
                    <UserX />
                  </Button>
                  <Button size="icon" variant="outline" title="Delete" aria-label={`Delete reference ${r.id}`} onClick={() => open({ type: "delete", ref: r })}>
                    <Trash2 />
                  </Button>
                </div>
              </CardContent>
            </Card>
          </li>
        ))}
      </ul>
    );
  }

  const spin = <Loader2 className="animate-spin" />;
  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader title="References" description="Teach Kino each kid's face: manage the reference faces it matches against, retire outliers and fix mistakes." />
      {kids.isPending ? (
        <Skeleton className="h-64 w-full" />
      ) : kids.isError ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-3 py-12 text-center">
            <p className="font-medium">Could not load kids</p>
            <p className="text-sm text-muted-foreground">{kids.error.message}</p>
            <Button variant="outline" onClick={() => kids.refetch()}>
              <RefreshCw /> Retry
            </Button>
          </CardContent>
        </Card>
      ) : !kid ? (
        <Card className="border-dashed">
          <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
            <p className="font-medium">No kids yet</p>
            <Button asChild variant="outline">
              <Link to="/kids">Add a kid</Link>
            </Button>
          </CardContent>
        </Card>
      ) : (
        <>
          <div className="mb-4 grid gap-4 md:grid-cols-2">
            <div className="space-y-3">
              <Field label="Kid">
                <select className={selectClass} value={kid.id} onChange={(e) => selectKid(e.target.value)}>
                  {kids.data.map((k) => (
                    <option key={k.id} value={k.id} dir="auto">
                      {k.display_name}
                    </option>
                  ))}
                </select>
              </Field>
              <Button asChild variant="outline" size="sm">
                <Link to={`/kids/${kid.id}/enroll`}>
                  <PlusCircle /> Add references
                </Link>
              </Button>
              <CoverageMeter kid={kid} />
            </div>
            <div className="space-y-1">
              <p className="text-xs font-medium text-muted-foreground">Photo dates by month</p>
              {refs.isPending ? <Skeleton className="h-20 w-full" /> : <DateHistogram data={histogram} />}
            </div>
          </div>
          <div className="mb-4 flex flex-wrap items-end gap-3" role="search" aria-label="Reference filters">
            <Field label="Source">
              <select className={selectClass} value={filters.source} onChange={(e) => set({ source: e.target.value })}>
                <option value="">All sources</option>
                {SOURCES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Status">
              <select className={selectClass} value={filters.status} onChange={(e) => set({ status: e.target.value })}>
                <option value="">Any status</option>
                {STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="From">
              <Input type="date" value={filters.from} max={filters.to || undefined} onChange={(e) => set({ from: e.target.value })} />
            </Field>
            <Field label="To">
              <Input type="date" value={filters.to} min={filters.from || undefined} onChange={(e) => set({ to: e.target.value })} />
            </Field>
            {outlierCount > 0 && (
              <Button
                variant={showOutliersOnly ? "default" : "outline"}
                aria-pressed={showOutliersOnly}
                onClick={() => setOutliersOnly((v) => !v)}
              >
                Outliers only ({outlierCount})
              </Button>
            )}
            {hasRefFilters(filters) && (
              <Button variant="ghost" onClick={() => setFilters(EMPTY_REF_FILTERS)}>
                Clear
              </Button>
            )}
          </div>
          {outlierCount > 0 && (
            <div role="status" className="mb-4 flex items-start gap-2 rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" aria-hidden="true" />
              <p>
                {outlierCount} {outlierCount === 1 ? "reference looks" : "references look"} different from the rest; review or retire{" "}
                {outlierCount === 1 ? "it" : "them"}.
              </p>
            </div>
          )}
          {body}
        </>
      )}

      <Dialog open={action?.type === "delete"} onOpenChange={(o) => !o && close()}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete this reference?</DialogTitle>
            <DialogDescription>
              The reference face is removed from <span dir="auto">{kid?.display_name}</span> and Kino will no longer match against it.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={close} disabled={busy}>Cancel</Button>
            <Button variant="destructive" disabled={busy} onClick={() => action && remove.mutate(action.ref)}>
              {remove.isPending && spin} Delete reference
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={action?.type === "negative"} onOpenChange={(o) => !o && close()}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Mark as someone else?</DialogTitle>
            <DialogDescription>
              This face is not <span dir="auto">{kid?.display_name}</span>. Kino keeps it as a negative example so similar faces
              are penalised, and the reference is removed from the references.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={close} disabled={busy}>Cancel</Button>
            <Button variant="destructive" disabled={busy} onClick={() => action && negative.mutate(action.ref)}>
              {negative.isPending && spin} Mark as someone else
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={action?.type === "move"} onOpenChange={(o) => !o && close()}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Move to another kid</DialogTitle>
            <DialogDescription>Pick the kid this face really belongs to.</DialogDescription>
          </DialogHeader>
          <Field label="Move to">
            <select className={selectClass} value={moveTo} onChange={(e) => setMoveTo(e.target.value)}>
              <option value="">Choose a kid…</option>
              {otherKids.map((k) => (
                <option key={k.id} value={k.id} dir="auto">
                  {k.display_name}
                </option>
              ))}
            </select>
          </Field>
          <DialogFooter>
            <Button variant="outline" onClick={close} disabled={busy}>Cancel</Button>
            <Button disabled={busy || !moveTo} onClick={() => action && move.mutate({ r: action.ref, to: Number(moveTo) })}>
              {move.isPending && spin} Move
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={action?.type === "date"} onOpenChange={(o) => !o && close()}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Photo date</DialogTitle>
            <DialogDescription>When the photo was taken. Older references weigh less in matching.</DialogDescription>
          </DialogHeader>
          <Field label="Date">
            <Input type="date" value={dateValue} onChange={(e) => setDateValue(e.target.value)} />
          </Field>
          <DialogFooter>
            <Button variant="outline" onClick={close} disabled={busy}>Cancel</Button>
            <Button variant="outline" disabled={busy} onClick={() => action && setDate.mutate({ r: action.ref, date: null })}>
              Clear date
            </Button>
            <Button disabled={busy || !dateValue} onClick={() => action && setDate.mutate({ r: action.ref, date: dateValue })}>
              {setDate.isPending && spin} Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
