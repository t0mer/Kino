import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Activity, Loader2, RefreshCw } from "lucide-react";
import { type ReactNode, useMemo, useState } from "react";
import { ImageDetailDialog } from "@/components/activity/ImageDetailDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/sonner";
import {
  activityApi,
  activityKeys,
  type ActivityFilters,
  EMPTY_FILTERS,
  flattenPages,
  formatMs,
  hasActiveFilters,
  IMAGE_STATUSES,
  matchSummary,
  statusLabel,
  statusTone,
} from "@/lib/activity";
import { formatDateTime, formatRelative } from "@/lib/format";
import { groupKeys, groupLabel, groupsApi } from "@/lib/groups";
import { useLive } from "@/lib/query";
import { usePageTitle } from "@/lib/usePageTitle";
import { PageHeader } from "./PlaceholderPage";

const selectClass =
  "h-9 w-full rounded-md border border-input bg-background px-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

type Tab = "images" | "failed";

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

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border bg-card px-3 py-2">
      <dd className="text-lg font-semibold tabular-nums">{value}</dd>
      <dt className="text-xs text-muted-foreground">{label}</dt>
    </div>
  );
}

function ImagesTab({ onOpen }: { onOpen: (id: number) => void }) {
  const [filters, setFilters] = useState<ActivityFilters>(EMPTY_FILTERS);
  const groups = useLive(groupKeys.list, groupsApi.list);
  const list = useInfiniteQuery({
    queryKey: activityKeys.list(filters),
    queryFn: ({ pageParam }) => activityApi.list(filters, pageParam),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next_cursor,
    // Only the first page is refreshed; loaded-more pages stay as fetched.
    refetchInterval: 60_000,
  });
  const all = useMemo(() => flattenPages(list.data?.pages ?? []), [list.data]);
  const set = (patch: Partial<ActivityFilters>) => setFilters((f) => ({ ...f, ...patch }));

  let body: ReactNode;
  if (list.isPending) {
    body = (
      <div className="space-y-2" aria-busy="true" aria-label="Loading activity">
        {Array.from({ length: 6 }, (_, i) => (
          <Skeleton key={i} className="h-16 w-full rounded-lg" />
        ))}
      </div>
    );
  } else if (list.isError) {
    body = <ErrorCard title="Could not load activity" message={list.error.message} onRetry={() => list.refetch()} />;
  } else if (all.length === 0) {
    body = (
      <Card className="border-dashed">
        <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-accent text-accent-foreground">
            <Activity className="h-6 w-6" aria-hidden="true" />
          </div>
          <p className="font-medium">{hasActiveFilters(filters) ? "No images match these filters" : "No images yet"}</p>
          <p className="max-w-sm text-sm text-muted-foreground">
            {hasActiveFilters(filters)
              ? "Try another status or group."
              : "Images appear here as they arrive from your watched groups."}
          </p>
          {hasActiveFilters(filters) && (
            <Button variant="outline" onClick={() => setFilters(EMPTY_FILTERS)}>
              Clear filters
            </Button>
          )}
        </CardContent>
      </Card>
    );
  } else {
    body = (
      <>
        <ul className="space-y-2">
          {all.map((im) => {
            const summary = matchSummary(im.matches);
            return (
              <li key={im.id}>
                <button
                  type="button"
                  onClick={() => onOpen(im.id)}
                  aria-label={`Open image ${im.id}`}
                  className="flex w-full items-center gap-3 rounded-lg border bg-card p-2 text-start transition hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <div className="h-14 w-14 shrink-0 overflow-hidden rounded-md bg-muted">
                    {im.has_file && (
                      <img src={`/api/images/${im.id}/file`} alt="" loading="lazy" className="h-full w-full object-cover" />
                    )}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span dir="auto" className="truncate text-sm font-medium">{im.group_name ?? "Unknown group"}</span>
                      <Badge variant={statusTone(im.status)}>{statusLabel(im.status, im.skip_reason)}</Badge>
                    </div>
                    <p className="truncate text-xs text-muted-foreground">
                      <span title={formatDateTime(im.received_at)}>{formatRelative(im.received_at)}</span>
                      {" · "}
                      {im.face_count} face{im.face_count === 1 ? "" : "s"}
                      {summary && <> · {summary}</>}
                      {" · "}
                      {formatMs(im.processing_ms)}
                    </p>
                  </div>
                </button>
              </li>
            );
          })}
        </ul>
        <div className="flex justify-center py-6">
          {list.hasNextPage ? (
            <Button variant="outline" onClick={() => list.fetchNextPage()} disabled={list.isFetchingNextPage}>
              {list.isFetchingNextPage && <Loader2 className="animate-spin" />} Load more
            </Button>
          ) : (
            <p className="text-xs text-muted-foreground">{all.length} image{all.length === 1 ? "" : "s"}</p>
          )}
        </div>
      </>
    );
  }

  return (
    <>
      <div className="mb-4 flex flex-wrap items-end gap-3" role="search" aria-label="Activity filters">
        <label className="flex min-w-36 flex-1 flex-col gap-1 text-xs font-medium text-muted-foreground">
          Status
          <select className={selectClass} value={filters.status} onChange={(e) => set({ status: e.target.value })}>
            <option value="">All statuses</option>
            {IMAGE_STATUSES.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </label>
        <label className="flex min-w-36 flex-1 flex-col gap-1 text-xs font-medium text-muted-foreground">
          Group
          <select className={selectClass} value={filters.groupId} onChange={(e) => set({ groupId: e.target.value })}>
            <option value="">All groups</option>
            {groups.data?.map((g) => (
              <option key={g.id} value={g.id} dir="auto">{groupLabel(g)}</option>
            ))}
          </select>
        </label>
        {hasActiveFilters(filters) && (
          <Button variant="ghost" onClick={() => setFilters(EMPTY_FILTERS)}>Clear</Button>
        )}
      </div>
      {body}
    </>
  );
}

function FailedTab({ onOpen }: { onOpen: (id: number) => void }) {
  const qc = useQueryClient();
  const failed = useLive(activityKeys.forwards("failed"), () => activityApi.forwards("failed"));
  const pending = useLive(activityKeys.forwards("pending"), () => activityApi.forwards("pending"));
  const retry = useMutation({
    mutationFn: (id: number) => activityApi.retry(id),
    onSuccess: () => toast.success("Forward queued for retry"),
    onError: (e) => toast.error("Could not retry the forward", { description: e.message }),
    onSettled: () => void qc.invalidateQueries({ queryKey: ["activity", "forwards"] }),
  });

  return (
    <div className="space-y-6">
      <section aria-label="Failed forwards">
        <h2 className="mb-2 text-sm font-semibold">Failed forwards</h2>
        {failed.isPending ? (
          <Skeleton className="h-20 w-full" aria-label="Loading failed forwards" />
        ) : failed.isError ? (
          <ErrorCard title="Could not load failed forwards" message={failed.error.message} onRetry={() => failed.refetch()} />
        ) : failed.data.length === 0 ? (
          <p className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">No failed forwards.</p>
        ) : (
          <ul className="space-y-2">
            {failed.data.map((f) => (
              <li key={f.id} className="flex flex-wrap items-center gap-3 rounded-lg border bg-card p-3 text-sm">
                <div className="min-w-0 flex-1">
                  <button type="button" className="font-medium underline-offset-2 hover:underline" onClick={() => onOpen(f.image_id)}>
                    Image #{f.image_id}
                  </button>
                  <p className="text-xs text-muted-foreground">
                    {f.attempts} attempt{f.attempts === 1 ? "" : "s"} · created {formatDateTime(f.created_at)}
                  </p>
                  {f.last_error && (
                    <p className="mt-1 whitespace-pre-wrap break-words text-xs text-destructive">{f.last_error}</p>
                  )}
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  aria-label={`Retry forward ${f.id}`}
                  disabled={retry.isPending && retry.variables === f.id}
                  onClick={() => retry.mutate(f.id)}
                >
                  {retry.isPending && retry.variables === f.id ? <Loader2 className="animate-spin" /> : <RefreshCw />} Retry
                </Button>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section aria-label="Pending forwards">
        <h2 className="mb-2 text-sm font-semibold">Pending forwards</h2>
        {pending.isPending ? (
          <Skeleton className="h-12 w-full" />
        ) : pending.isError ? (
          <ErrorCard title="Could not load pending forwards" message={pending.error.message} onRetry={() => pending.refetch()} />
        ) : pending.data.length === 0 ? (
          <p className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">Nothing waiting to be sent.</p>
        ) : (
          <ul className="space-y-2">
            {pending.data.map((f) => (
              <li key={f.id} className="rounded-lg border bg-card p-3 text-sm">
                <button type="button" className="font-medium underline-offset-2 hover:underline" onClick={() => onOpen(f.image_id)}>
                  Image #{f.image_id}
                </button>
                <p className="text-xs text-muted-foreground">
                  {f.attempts} attempt{f.attempts === 1 ? "" : "s"} · created {formatDateTime(f.created_at)}
                  {f.next_attempt_at && <> · next try {formatRelative(f.next_attempt_at)}</>}
                </p>
                {f.last_error && <p className="mt-1 whitespace-pre-wrap break-words text-xs text-destructive">{f.last_error}</p>}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

export function ActivityPage() {
  usePageTitle("Activity");
  const [tab, setTab] = useState<Tab>("images");
  const [open, setOpen] = useState<number | null>(null);
  const stats = useLive(activityKeys.stats, activityApi.stats);
  const failedCount = stats.data?.forwards.failed ?? 0;

  const tabClass = (t: Tab) =>
    `inline-flex items-center gap-2 border-b-2 px-3 py-2 text-sm font-medium ${
      tab === t ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"
    }`;

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="Activity" description="Recent images, their pipeline outcome and forward delivery." />
      {stats.data && (
        <dl className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-4" aria-label="Statistics">
          <Stat label="Images processed" value={stats.data.images.done ?? 0} />
          <Stat label="Queued" value={stats.data.queue_depth} />
          <Stat label="Review pending" value={stats.data.review_count} />
          <Stat label="Forwards waiting" value={stats.data.forward_queue_depth} />
        </dl>
      )}
      <div role="tablist" aria-label="Activity views" className="mb-4 flex border-b">
        <button type="button" role="tab" aria-selected={tab === "images"} className={tabClass("images")} onClick={() => setTab("images")}>
          Images
        </button>
        <button type="button" role="tab" aria-selected={tab === "failed"} className={tabClass("failed")} onClick={() => setTab("failed")}>
          Forwards
          {failedCount > 0 && (
            <Badge variant="destructive" aria-label={`${failedCount} failed forwards`}>{failedCount}</Badge>
          )}
        </button>
      </div>
      {tab === "images" ? <ImagesTab onOpen={setOpen} /> : <FailedTab onOpen={setOpen} />}
      <ImageDetailDialog imageId={open} onClose={() => setOpen(null)} />
    </div>
  );
}
