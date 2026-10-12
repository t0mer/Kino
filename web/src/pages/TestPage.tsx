import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Loader2, RefreshCw, RotateCw, ShieldCheck } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router";
import { BucketColumn } from "@/components/dryrun/BucketColumn";
import { FlagDialog } from "@/components/dryrun/FlagDialog";
import { ThresholdPanel } from "@/components/dryrun/ThresholdPanel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/sonner";
import { isApiError } from "@/lib/api";
import { useDebouncedValue } from "@/lib/debounce";
import {
  BUCKETS,
  type BucketName,
  defaultRange,
  DEFAULT_RANGE_DAYS,
  dryrunApi,
  dryrunKeys,
  invalidRange,
  parseDays,
  rescoreSummary,
  type ScoreFace,
  thresholdChanges,
} from "@/lib/dryrun";
import { kidKeys, kidsApi } from "@/lib/kids";
import { useLive } from "@/lib/query";
import {
  fieldError,
  isDirty,
  type ParsedErrors,
  parseFieldErrors,
  settingsApi,
  settingsKeys,
  type SettingsOut,
  toThresholds,
  type Thresholds,
  validateThresholds,
} from "@/lib/settings";
import { usePageTitle } from "@/lib/usePageTitle";
import { PageHeader } from "./PlaceholderPage";

const DEBOUNCE_MS = 300;
const inputClass =
  "h-9 w-full rounded-md border border-input bg-background px-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

function ErrorCard({ title, message, onRetry }: { title: string; message: string; onRetry: () => void }) {
  return (
    <Card>
      <CardContent className="flex flex-col items-center gap-3 py-12 text-center">
        <p className="font-medium">{title}</p>
        <p className="text-sm text-muted-foreground">{message}</p>
        <Button variant="outline" onClick={onRetry}>
          <RefreshCw /> Retry
        </Button>
      </CardContent>
    </Card>
  );
}

const errText = (e: unknown, fallback: string) => (isApiError(e) ? e.detail : fallback);

type Confirm = "apply" | "autosave" | "rescore" | null;

export function TestPage() {
  usePageTitle("Test");
  const qc = useQueryClient();
  const settings = useLive<SettingsOut>(settingsKeys.all, settingsApi.get);
  const kids = useLive(kidKeys.list, kidsApi.list);

  const [kidId, setKidId] = useState("");
  const [range, setRange] = useState(() => defaultRange());
  const [draft, setDraft] = useState<Thresholds | null>(null);
  const [serverErrors, setServerErrors] = useState<ParsedErrors | null>(null);
  const [confirm, setConfirm] = useState<Confirm>(null);
  const [days, setDays] = useState(String(DEFAULT_RANGE_DAYS));
  const [picked, setPicked] = useState<{ face: ScoreFace; bucket: BucketName } | null>(null);

  const saved = useMemo(() => (settings.data ? toThresholds(settings.data.thresholds) : null), [settings.data]);
  const floor = settings.data?.thresholds.auto_floor ?? 0;
  const effective = draft ?? saved;
  const local = effective ? validateThresholds(effective, floor) : {};
  const dirty = saved !== null && isDirty(draft, saved);
  const rangeBad = invalidRange(range.from, range.to);

  // Re-score server-side ~300 ms after the last change; a stale request is aborted by react-query.
  const wanted = JSON.stringify({ kidId, from: range.from, to: range.to, t: effective });
  const settled = useDebouncedValue(wanted, DEBOUNCE_MS);
  const req = useMemo(() => {
    const p = JSON.parse(settled) as { kidId: string; from: string; to: string; t: Thresholds | null };
    return p.t ? { kidId: p.kidId, from: p.from, to: p.to, thresholds: p.t } : null;
  }, [settled]);
  const reqValid = req !== null && Object.keys(validateThresholds(req.thresholds, floor)).length === 0 && !invalidRange(req.from, req.to);

  const score = useQuery({
    queryKey: req ? dryrunKeys.score(req) : ["test", "score", "idle"],
    queryFn: ({ signal }) => dryrunApi.score(req!, signal),
    enabled: reqValid,
    placeholderData: keepPreviousData,
    staleTime: 0,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const updating = settled !== wanted || score.isFetching;

  const kid = kids.data?.find((k) => String(k.id) === kidId) ?? null;

  const apply = useMutation({
    mutationFn: (t: Thresholds) => settingsApi.putThresholds(t),
    onSuccess: (out) => {
      qc.setQueryData<SettingsOut>(settingsKeys.all, (old) => (old ? { ...old, thresholds: out } : old));
      setDraft(null);
      setServerErrors(null);
      setConfirm(null);
      toast.success("Thresholds applied.");
    },
    onError: (e) => {
      setConfirm(null);
      setServerErrors(parseFieldErrors(errText(e, "Could not apply the thresholds.")));
      toast.error("The server rejected these thresholds.");
    },
  });
  const enable = useMutation({
    mutationFn: (id: number) => kidsApi.update(id, { auto_save_enabled: true }),
    onSuccess: (k) => {
      void qc.invalidateQueries({ queryKey: kidKeys.all });
      setConfirm(null);
      toast.success(`Auto-save enabled for ${k.display_name}.`);
    },
    onError: (e) => {
      setConfirm(null);
      toast.error(errText(e, "Could not enable auto-save."));
    },
  });
  const rescore = useMutation({
    mutationFn: (n: number) => dryrunApi.rescore(n),
    onSuccess: (r) => {
      setConfirm(null);
      toast.success(rescoreSummary(r));
      void qc.invalidateQueries({ queryKey: dryrunKeys.all });
      void qc.invalidateQueries({ queryKey: ["review"] });
    },
    onError: (e) => {
      setConfirm(null);
      toast.error(errText(e, "Re-score failed."));
    },
  });

  const edit = (t: Thresholds) => {
    setDraft(t);
    setServerErrors(null);
  };
  const daysN = parseDays(days);
  const generalErrors = serverErrors?.general ?? [];
  const otherFieldErrors = serverErrors
    ? Object.keys(serverErrors.fields).filter((k) => !/^[LMS]\./.test(k) && !/^[LMS]$/.test(k))
    : [];
  const changes = saved && effective ? thresholdChanges(saved, effective) : [];

  if (settings.isPending || kids.isPending) {
    return (
      <div>
        <PageHeader title="Test" description="Dry-run thresholds against stored faces before enabling auto-save." />
        <div className="space-y-3" aria-busy="true" aria-label="Loading">
          <Skeleton className="h-40 w-full rounded-lg" />
          <Skeleton className="h-64 w-full rounded-lg" />
        </div>
      </div>
    );
  }
  if (settings.isError || kids.isError || !saved || !effective) {
    return (
      <div>
        <PageHeader title="Test" />
        <ErrorCard
          title="Could not load the Test page"
          message={(settings.error ?? kids.error)?.message ?? "Unexpected error"}
          onRetry={() => {
            void settings.refetch();
            void kids.refetch();
          }}
        />
      </div>
    );
  }

  const data = score.data;
  let results;
  if (rangeBad) {
    results = <p role="alert" className="text-sm text-destructive">The start date is after the end date.</p>;
  } else if (Object.keys(local).length > 0) {
    results = <p className="text-sm text-muted-foreground">Fix the highlighted thresholds to see results.</p>;
  } else if (score.isError && !data) {
    results = (
      <ErrorCard title="Could not score" message={errText(score.error, "Unexpected error")} onRetry={() => void score.refetch()} />
    );
  } else if (!data) {
    results = (
      <div className="grid gap-3 lg:grid-cols-3" aria-busy="true" aria-label="Scoring">
        {BUCKETS.map((b) => (
          <Skeleton key={b} className="h-56 rounded-lg" />
        ))}
      </div>
    );
  } else if (data.examined === 0) {
    results = (
      <Card>
        <CardContent className="py-12 text-center">
          <p className="font-medium">No faces in this range</p>
          <p className="mt-1 text-sm text-muted-foreground">
            Widen the date range, or wait for photos from watched groups to be processed.
          </p>
        </CardContent>
      </Card>
    );
  } else {
    results = (
      <div className="space-y-3">
        {data.truncated && (
          <p className="flex items-center gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm">
            <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
            Only the newest faces in this range were scored. Narrow the dates for a complete result.
          </p>
        )}
        {score.isError && (
          <p role="alert" className="text-sm text-destructive">
            The last re-score failed ({errText(score.error, "error")}); showing the previous result.{" "}
            <button type="button" className="underline" onClick={() => void score.refetch()}>
              Retry
            </button>
          </p>
        )}
        <div className="relative">
          <div className={`grid gap-3 transition-opacity lg:grid-cols-3 ${updating ? "opacity-60" : ""}`} aria-busy={updating}>
            {BUCKETS.map((b) => (
              <BucketColumn key={b} name={b} bucket={data.buckets[b]} onPick={(face) => setPicked({ face, bucket: b })} />
            ))}
          </div>
          {updating && (
            <div className="pointer-events-none absolute right-3 top-3" role="status" aria-label="Re-scoring">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <PageHeader title="Test" description="Dry-run thresholds against stored faces before enabling auto-save. Nothing is saved until you apply." />

      <Card>
        <CardContent className="grid gap-6 pt-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
          <div className="space-y-4">
            <div>
              <label htmlFor="dry-kid" className="mb-1 block text-sm font-medium">Kid</label>
              <select id="dry-kid" className={inputClass} value={kidId} onChange={(e) => setKidId(e.target.value)}>
                <option value="">All kids</option>
                {kids.data.map((k) => (
                  <option key={k.id} value={k.id} dir="auto">
                    {k.display_name}
                  </option>
                ))}
              </select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label htmlFor="dry-from" className="mb-1 block text-sm font-medium">From</label>
                <input id="dry-from" type="date" className={inputClass} value={range.from} max={range.to || undefined}
                  onChange={(e) => setRange((r) => ({ ...r, from: e.target.value }))} />
              </div>
              <div>
                <label htmlFor="dry-to" className="mb-1 block text-sm font-medium">To</label>
                <input id="dry-to" type="date" className={inputClass} value={range.to} min={range.from || undefined}
                  onChange={(e) => setRange((r) => ({ ...r, to: e.target.value }))} />
              </div>
            </div>

            {kid && kid.reference_count === 0 && (
              <p className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm">
                <span dir="auto" className="font-medium">{kid.display_name}</span> has no reference faces yet, so nothing can match.{" "}
                <Link className="underline" to={`/kids/${kid.id}/enroll`}>Enroll faces</Link>
              </p>
            )}

            {kid && (
              <div className="flex flex-wrap items-center gap-2">
                {kid.auto_save_enabled ? (
                  <Button variant="outline" disabled>
                    <ShieldCheck /> Auto-save is on for <span dir="auto">{kid.display_name}</span>
                  </Button>
                ) : (
                  <Button variant="outline" onClick={() => setConfirm("autosave")}>
                    <ShieldCheck /> Enable auto-save for <span dir="auto">{kid.display_name}</span>
                  </Button>
                )}
              </div>
            )}

            <div className="border-t pt-4">
              <label htmlFor="dry-days" className="mb-1 block text-sm font-medium">Re-score stored faces</label>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm text-muted-foreground">Last</span>
                <input id="dry-days" type="number" min={1} max={365} className={`${inputClass} !w-20 shrink-0`} value={days}
                  aria-invalid={daysN === null} onChange={(e) => setDays(e.target.value)} />
                <span className="text-sm text-muted-foreground">days</span>
                <Button variant="outline" disabled={daysN === null || rescore.isPending} onClick={() => setConfirm("rescore")}>
                  {rescore.isPending ? <Loader2 className="animate-spin" /> : <RotateCw />} Re-score last {daysN ?? "N"} days
                </Button>
              </div>
              {daysN === null && <p role="alert" className="mt-1 text-xs text-destructive">Enter a whole number of days from 1 to 365.</p>}
            </div>
          </div>

          <div className="space-y-3">
            <ThresholdPanel value={effective} local={local} server={serverErrors} onChange={edit} />
            {generalErrors.map((m) => (
              <p key={m} role="alert" className="text-sm text-destructive">{m}</p>
            ))}
            {otherFieldErrors.map((k) => (
              <p key={k} role="alert" className="text-sm text-destructive">{fieldError(serverErrors!, k)}</p>
            ))}
            <div className="flex flex-wrap items-center gap-2">
              <Button disabled={!dirty || Object.keys(local).length > 0 || apply.isPending} onClick={() => setConfirm("apply")}>
                Apply thresholds
              </Button>
              <Button variant="ghost" disabled={!dirty} onClick={() => { setDraft(null); setServerErrors(null); }}>
                Reset
              </Button>
              {dirty && <Badge variant="outline">Unsaved</Badge>}
            </div>
          </div>
        </CardContent>
      </Card>

      {results}

      <FlagDialog
        face={picked?.face ?? null}
        bucket={picked?.bucket ?? null}
        onClose={() => setPicked(null)}
        onDone={() => {
          setPicked(null);
          void qc.invalidateQueries({ queryKey: dryrunKeys.all });
          void qc.invalidateQueries({ queryKey: ["review"] });
        }}
      />

      <Dialog open={confirm === "apply"} onOpenChange={(o) => !o && !apply.isPending && setConfirm(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Apply these thresholds?</DialogTitle>
            <DialogDescription>They take effect for every new photo and for the next re-score.</DialogDescription>
          </DialogHeader>
          <ul className="space-y-1 text-sm">
            {changes.map((c) => (
              <li key={c.path} className="flex justify-between gap-3">
                <span>{c.label}</span>
                <span className="tabular-nums">{c.from.toFixed(2)} → {c.to.toFixed(2)}</span>
              </li>
            ))}
          </ul>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirm(null)} disabled={apply.isPending}>Cancel</Button>
            <Button onClick={() => apply.mutate(effective)} disabled={apply.isPending}>
              {apply.isPending && <Loader2 className="animate-spin" />} Apply
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={confirm === "autosave"} onOpenChange={(o) => !o && !enable.isPending && setConfirm(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Enable auto-save for <span dir="auto">{kid?.display_name}</span>?</DialogTitle>
            <DialogDescription>
              Matches above the auto-save thresholds are saved and forwarded without review from now on. Until then they go to the review queue.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirm(null)} disabled={enable.isPending}>Cancel</Button>
            <Button onClick={() => kid && enable.mutate(kid.id)} disabled={enable.isPending}>
              {enable.isPending && <Loader2 className="animate-spin" />} Enable auto-save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={confirm === "rescore"} onOpenChange={(o) => !o && !rescore.isPending && setConfirm(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Re-score the last {daysN} days?</DialogTitle>
            <DialogDescription>
              Matching is re-run on stored faces with the applied thresholds. Your own decisions are kept. This can take a while.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirm(null)} disabled={rescore.isPending}>Cancel</Button>
            <Button onClick={() => daysN && rescore.mutate(daysN)} disabled={rescore.isPending}>
              {rescore.isPending && <Loader2 className="animate-spin" />} Re-score
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
