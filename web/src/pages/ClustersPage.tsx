import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw, ScanFace, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { ClusterImport } from "@/components/backfill/ClusterImport";
import { ClusterDialog } from "@/components/clusters/ClusterDialog";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/sonner";
import { clusterApi, clusterKeys, dateRange, faceCountLabel, groupsSummary, SAMPLE_COUNT } from "@/lib/clusters";
import { kidKeys, kidsApi } from "@/lib/kids";
import { useLive } from "@/lib/query";
import { usePageTitle } from "@/lib/usePageTitle";
import { PageHeader } from "./PlaceholderPage";

export const RUN_REFETCH_DELAY_MS = 3000;

export function ClustersPage() {
  usePageTitle("Who is this?");
  const qc = useQueryClient();
  const [openId, setOpenId] = useState<number | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);

  const kids = useLive(kidKeys.list, kidsApi.list);
  const list = useInfiniteQuery({
    queryKey: clusterKeys.list,
    queryFn: ({ pageParam }) => clusterApi.list(pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
  });
  const clusters = list.data?.pages.flatMap((p) => p.items) ?? [];

  const run = useMutation({
    mutationFn: clusterApi.run,
    onSuccess: () => {
      toast.success("Looking for clusters", { description: "This can take a minute; the list refreshes shortly." });
      clearTimeout(timer.current);
      timer.current = setTimeout(() => void qc.invalidateQueries({ queryKey: clusterKeys.list }), RUN_REFETCH_DELAY_MS);
    },
    onError: (e) => toast.error("Could not start clustering", { description: e.message }),
  });

  const noKids = kids.isSuccess && kids.data.length === 0;

  let body;
  if (list.isPending) {
    body = (
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" aria-busy="true" aria-label="Loading clusters">
        {Array.from({ length: 6 }, (_, i) => (
          <Skeleton key={i} className="h-48 rounded-xl" />
        ))}
      </div>
    );
  } else if (list.isError) {
    body = (
      <Card>
        <CardContent className="flex flex-col items-center gap-3 py-12 text-center">
          <p className="font-medium">Could not load clusters</p>
          <p className="text-sm text-muted-foreground">{list.error.message}</p>
          <Button variant="outline" onClick={() => list.refetch()}>
            <RefreshCw /> Retry
          </Button>
        </CardContent>
      </Card>
    );
  } else if (clusters.length === 0) {
    body = (
      <Card className="border-dashed">
        <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-accent text-accent-foreground">
            <ScanFace className="h-6 w-6" aria-hidden="true" />
          </div>
          <p className="font-medium">No unknown faces to label yet</p>
          <p className="max-w-sm text-sm text-muted-foreground">
            Import the last 30 days from your groups, then label the clusters.
          </p>
          {noKids && (
            <p className="max-w-sm text-sm">
              You have no kids yet. <Link className="font-medium underline" to="/kids">Add a kid first</Link> so you can label clusters.
            </p>
          )}
          <ClusterImport onFindClusters={() => run.mutate()} finding={run.isPending} />
        </CardContent>
      </Card>
    );
  } else {
    body = (
      <>
        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {clusters.map((c) => {
            const groups = groupsSummary(c.groups);
            const range = dateRange(c.date_from, c.date_to);
            return (
              <li key={c.id}>
                <button
                  type="button"
                  data-testid={`cluster-${c.id}`}
                  aria-label={`Open cluster of ${faceCountLabel(c.size)}`}
                  onClick={() => setOpenId(c.id)}
                  className="flex w-full flex-col gap-2 rounded-xl border bg-card p-3 text-start shadow-sm transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <div className="grid grid-cols-3 gap-1">
                    {c.sample_crop_urls.slice(0, SAMPLE_COUNT).map((u) => (
                      <img key={u} src={u} alt="" loading="lazy" className="aspect-square w-full rounded-md bg-muted object-cover" />
                    ))}
                  </div>
                  <span className="font-medium">{faceCountLabel(c.size)}</span>
                  {groups && <span className="truncate text-xs text-muted-foreground" dir="auto">{groups}</span>}
                  {range && <span className="text-xs text-muted-foreground">{range}</span>}
                </button>
              </li>
            );
          })}
        </ul>
        {list.hasNextPage && (
          <div className="mt-4 flex justify-center">
            <Button variant="outline" onClick={() => list.fetchNextPage()} disabled={list.isFetchingNextPage}>
              {list.isFetchingNextPage && <Loader2 className="animate-spin" />} Load more
            </Button>
          </div>
        )}
      </>
    );
  }

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="Who is this?" description="Label clusters of unknown faces to teach Kino quickly." />
      <div className="mb-4 flex justify-end">
        <Button variant="outline" onClick={() => run.mutate()} disabled={run.isPending}>
          {run.isPending ? <Loader2 className="animate-spin" /> : <Sparkles />} Find clusters now
        </Button>
      </div>
      {body}
      {openId !== null && <ClusterDialog clusterId={openId} kids={kids.data ?? []} onClose={() => setOpenId(null)} />}
    </div>
  );
}
