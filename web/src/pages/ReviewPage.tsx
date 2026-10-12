import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCheck, LayoutGrid, RefreshCw, Rows3 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BulkGrid } from "@/components/review/BulkGrid";
import { ReviewCard } from "@/components/review/ReviewCard";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/sonner";
import { kidKeys, kidsApi } from "@/lib/kids";
import {
  bulkSettled,
  bulkSummary,
  decisionInvalidations,
  goneMessage,
  invalidateKeys,
  isGone,
  MAX_NUMBERED_KIDS,
  needsPrefetch,
  queueOrder,
  type ReviewAction,
  reviewApi,
  reviewKeys,
  selectRange,
  shortcutFor,
  shouldIgnoreKey,
  withSkipped,
} from "@/lib/review";
import { usePageTitle } from "@/lib/usePageTitle";
import { PageHeader } from "./PlaceholderPage";

const selectClass =
  "h-9 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

const ACTION_LABEL: Record<ReviewAction, string> = {
  confirm: "Confirmed",
  reject: "Rejected",
  someone_else: "Rejected and remembered as someone else",
  reassign: "Reassigned",
};

export function ReviewPage() {
  usePageTitle("Review");
  const qc = useQueryClient();
  const [kidId, setKidId] = useState<number | null>(null);
  const [method, setMethod] = useState<string | null>(null);
  const [bulk, setBulk] = useState(false);
  const [decided, setDecided] = useState<ReadonlySet<number>>(new Set());
  const [skipped, setSkipped] = useState<number[]>([]);
  const [selected, setSelected] = useState<ReadonlySet<number>>(new Set());
  const lastToggled = useRef<number | null>(null);

  const kidsQ = useQuery({ queryKey: kidKeys.list, queryFn: kidsApi.list });
  const kids = useMemo(() => (kidsQ.data ?? []).filter((k) => k.active), [kidsQ.data]);
  const numberedIds = useMemo(() => kids.slice(0, MAX_NUMBERED_KIDS).map((k) => k.id), [kids]);

  const q = useInfiniteQuery({
    queryKey: reviewKeys.list(kidId, method),
    queryFn: ({ pageParam }) => reviewApi.page(kidId, method, pageParam),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next_cursor,
  });

  const all = useMemo(() => q.data?.pages.flatMap((p) => p.items) ?? [], [q.data]);
  const order = useMemo(() => queueOrder(all, decided, skipped), [all, decided, skipped]);
  const current = order[0];

  // Reset per-view state when the filters change.
  useEffect(() => {
    setDecided(new Set());
    setSkipped([]);
    setSelected(new Set());
    lastToggled.current = null;
  }, [kidId, method]);

  // Prefetch the next page while the user works through this one; also fetch when drained.
  const { hasNextPage, isFetchingNextPage, isError: listError, fetchNextPage } = q;
  useEffect(() => {
    if (q.isPending || isFetchingNextPage || listError) return;
    if (needsPrefetch(order.length, hasNextPage)) void fetchNextPage();
  }, [order.length, hasNextPage, isFetchingNextPage, listError, q.isPending, fetchNextPage]);

  const settle = useCallback(
    (ids: number[]) => {
      setDecided((d) => new Set([...d, ...ids]));
      setSelected((s) => new Set([...s].filter((x) => !ids.includes(x))));
      invalidateKeys(qc, decisionInvalidations);
    },
    [qc],
  );

  const decide = useMutation({
    mutationFn: (v: { matchId: number; action: ReviewAction; kidId?: number }) =>
      reviewApi.decide(v.matchId, v.action, v.kidId),
    onSuccess: (_r, v) => {
      toast.success(ACTION_LABEL[v.action]);
      settle([v.matchId]);
    },
    onError: (e, v) => {
      if (isGone(e)) {
        toast.warning(goneMessage(e));
        settle([v.matchId]);
      } else {
        toast.error("Could not save the decision", { description: e.message });
      }
    },
  });

  const bulkMut = useMutation({
    mutationFn: (v: { ids: number[]; action: "confirm" | "reject" }) => reviewApi.bulk(v.ids, v.action),
    onSuccess: ({ results }, v) => {
      const { ok, failed } = bulkSummary(results);
      settle(bulkSettled(results));
      if (failed.length === 0) toast.success(`${v.action === "confirm" ? "Confirmed" : "Rejected"} ${ok} photos`);
      else
        toast.warning(`${ok} done, ${failed.length} not applied`, {
          description: failed.map((f) => `#${f.match_id}: ${f.detail ?? f.status}`).join("; "),
        });
    },
    onError: (e) => toast.error("Bulk action failed", { description: e.message }),
  });

  const busy = decide.isPending || bulkMut.isPending;

  const act = useCallback(
    (action: ReviewAction, kid?: number) => {
      if (!current || busy) return;
      decide.mutate({ matchId: current.match_id, action, kidId: kid });
    },
    [current, busy, decide],
  );
  const skip = useCallback(() => {
    if (current) setSkipped((s) => withSkipped(s, current.match_id));
  }, [current]);

  useEffect(() => {
    if (bulk || !current) return;
    const onKey = (e: KeyboardEvent) => {
      if (shouldIgnoreKey(e)) return;
      const sc = shortcutFor(e.key, numberedIds, current.kid_id);
      if (!sc) return;
      e.preventDefault();
      if (sc.type === "skip") skip();
      else if (sc.type === "reassign") act("reassign", sc.kidId);
      else act(sc.action);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [bulk, current, numberedIds, act, skip]);

  const toggle = (id: number, shift: boolean) => {
    const from = shift ? lastToggled.current : null; // read now: the updater runs later
    const ids = order.map((i) => i.match_id);
    setSelected((s) => selectRange(ids, s, id, from));
    lastToggled.current = id;
  };

  const filters = (
    <div className="mb-5 flex flex-wrap items-center gap-3">
      <label className="flex items-center gap-2 text-sm">
        Kid
        <select
          className={selectClass}
          value={kidId ?? ""}
          onChange={(e) => setKidId(e.target.value ? Number(e.target.value) : null)}
          aria-label="Filter by kid"
          dir="auto"
        >
          <option value="">All kids</option>
          {kids.map((k) => (
            <option key={k.id} value={k.id}>
              {k.display_name}
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-2 text-sm">
        Method
        <select
          className={selectClass}
          value={method ?? ""}
          onChange={(e) => setMethod(e.target.value || null)}
          aria-label="Filter by method"
        >
          <option value="">All</option>
          <option value="face">Face</option>
          <option value="burst">Burst</option>
        </select>
      </label>
      <div className="ms-auto flex gap-1 rounded-lg border p-0.5" role="group" aria-label="View mode">
        <Button size="sm" variant={bulk ? "ghost" : "secondary"} aria-pressed={!bulk} onClick={() => setBulk(false)}>
          <Rows3 /> One by one
        </Button>
        <Button size="sm" variant={bulk ? "secondary" : "ghost"} aria-pressed={bulk} onClick={() => setBulk(true)}>
          <LayoutGrid /> Bulk
        </Button>
      </div>
    </div>
  );

  let body;
  if (q.isPending) {
    body = <Skeleton className="h-96 rounded-xl" aria-busy="true" aria-label="Loading review queue" />;
  } else if (q.isError) {
    body = (
      <Card>
        <CardContent className="flex flex-col items-center gap-3 py-12 text-center">
          <p className="font-medium">Could not load the review queue</p>
          <p className="text-sm text-muted-foreground">{q.error.message}</p>
          <Button variant="outline" onClick={() => q.refetch()}>
            <RefreshCw /> Retry
          </Button>
        </CardContent>
      </Card>
    );
  } else if (order.length === 0 && !q.hasNextPage) {
    body = (
      <Card className="border-dashed">
        <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-accent text-accent-foreground">
            <CheckCheck className="h-6 w-6" aria-hidden="true" />
          </div>
          <p className="font-medium">All caught up</p>
          <p className="max-w-sm text-sm text-muted-foreground">
            Nothing is waiting for review. New uncertain matches will show up here.
          </p>
          <Button
            variant="outline"
            onClick={() => {
              setDecided(new Set());
              setSkipped([]);
              void q.refetch();
            }}
          >
            <RefreshCw /> Check again
          </Button>
        </CardContent>
      </Card>
    );
  } else if (order.length === 0) {
    body = <Skeleton className="h-96 rounded-xl" aria-busy="true" aria-label="Loading more" />;
  } else if (bulk) {
    body = (
      <div className="space-y-4">
        <BulkGrid
          items={order}
          selected={selected}
          busy={busy}
          onToggle={toggle}
          onSelectAll={(on) => setSelected(on ? new Set(order.map((i) => i.match_id)) : new Set())}
          onAct={(action) => bulkMut.mutate({ ids: [...selected], action })}
        />
        {q.hasNextPage && (
          <div className="flex justify-center">
            <Button variant="outline" disabled={q.isFetchingNextPage} onClick={() => q.fetchNextPage()}>
              Load more
            </Button>
          </div>
        )}
      </div>
    );
  } else {
    body = (
      <ReviewCard
        item={current!}
        kids={kids}
        busy={busy}
        onConfirm={() => act("confirm")}
        onReject={() => act("reject")}
        onSomeoneElse={() => act("someone_else")}
        onReassign={(id) => act("reassign", id)}
        onSkip={skip}
      />
    );
  }

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        title="Review"
        description={`Confirm or reject uncertain matches. ${order.length > 0 ? `${order.length}${q.hasNextPage ? "+" : ""} loaded.` : ""}`}
      />
      {filters}
      {body}
    </div>
  );
}
