import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Loader2, RefreshCw, Scissors, Trash2, UserCheck } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/sonner";
import { isApiError } from "@/lib/api";
import { clusterApi, clusterKeys, faceCountLabel, labelFaceIds, labelToast } from "@/lib/clusters";
import type { Kid } from "@/lib/kids";
import { cn } from "@/lib/utils";

const selectClass =
  "h-9 w-full rounded-md border border-input bg-background px-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

interface Props {
  clusterId: number;
  kids: Kid[];
  onClose: () => void;
}

export function ClusterDialog({ clusterId, kids, onClose }: Props) {
  const qc = useQueryClient();
  const [deselected, setDeselected] = useState<ReadonlySet<number>>(new Set());
  const [kidId, setKidId] = useState("");
  const [confirmIgnore, setConfirmIgnore] = useState(false);

  const detail = useInfiniteQuery({
    queryKey: clusterKeys.detail(clusterId),
    queryFn: ({ pageParam }) => clusterApi.get(clusterId, pageParam),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next_cursor,
    retry: false,
  });
  const faces = useMemo(() => detail.data?.pages.flatMap((p) => p.faces) ?? [], [detail.data]);
  const loadedIds = useMemo(() => faces.map((f) => f.face_id), [faces]);
  const total = detail.data?.pages[0]?.size ?? faces.length;
  const more = detail.hasNextPage;
  const selectedCount = more && deselected.size === 0 ? total : loadedIds.filter((id) => !deselected.has(id)).length;

  const refreshList = () => void qc.invalidateQueries({ queryKey: clusterKeys.list });
  const stale = () => {
    toast.info("This cluster changed; the list was refreshed");
    onClose();
    refreshList();
  };
  const handle = (title: string) => (e: Error) => {
    if (isApiError(e, 404)) return stale();
    if (isApiError(e, 409) && /already/.test(e.message)) {
      toast.error("This cluster was already handled", { description: e.message });
      onClose();
      return refreshList();
    }
    toast.error(title, { description: e.message });
  };

  // A failed initial load of a vanished cluster is the same "stale" case.
  const loadError = detail.isError ? detail.error : null;
  const loadStale = loadError && isApiError(loadError, 404);

  useEffect(() => {
    if (loadStale) stale();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadStale]);

  const label = useMutation({
    mutationFn: () => clusterApi.label(clusterId, Number(kidId), labelFaceIds(loadedIds, deselected, !!more)),
    onSuccess: (r) => {
      const kidName = kids.find((k) => k.id === r.kid_id)?.display_name ?? "the kid";
      const t = labelToast(r, kidName);
      toast.success(t.title, { description: t.description });
      void qc.invalidateQueries({ queryKey: ["kids"] });
      onClose();
      refreshList();
    },
    onError: handle("Could not label the cluster"),
  });
  const ignore = useMutation({
    mutationFn: () => clusterApi.ignore(clusterId),
    onSuccess: (r) => {
      toast.success("Cluster ignored", {
        description: `${r.faces_deleted} faces and their embeddings were deleted.`,
      });
      setConfirmIgnore(false);
      onClose();
      refreshList();
    },
    onError: (e) => {
      setConfirmIgnore(false);
      handle("Could not ignore the cluster")(e);
    },
  });
  const split = useMutation({
    mutationFn: () => clusterApi.split(clusterId),
    onSuccess: (r) => {
      toast.success(`Split into ${r.cluster_ids.length} clusters`);
      onClose();
      refreshList();
    },
    onError: (e) => {
      if (isApiError(e, 409) && /split/.test(e.message)) {
        toast.info("This cluster cannot be split any further");
        return;
      }
      handle("Could not split the cluster")(e);
    },
  });

  const busy = label.isPending || ignore.isPending || split.isPending;
  const toggle = (id: number) =>
    setDeselected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const activeKids = kids.filter((k) => k.active);
  const canLabel = !busy && kidId !== "" && selectedCount > 0 && !detail.isPending;

  return (
    <>
      <Dialog open onOpenChange={(o) => !o && !busy && onClose()}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>Who is this?</DialogTitle>
            <DialogDescription>
              Click a face that does not belong to deselect it, then choose the kid.
            </DialogDescription>
          </DialogHeader>

          {detail.isPending ? (
            <div className="grid grid-cols-4 gap-2 sm:grid-cols-6" aria-busy="true" aria-label="Loading faces">
              {Array.from({ length: 12 }, (_, i) => (
                <Skeleton key={i} className="aspect-square rounded-md" />
              ))}
            </div>
          ) : loadError ? (
            <div className="flex flex-col items-center gap-3 py-8 text-center">
              <p className="font-medium">{loadStale ? "This cluster no longer exists" : "Could not load the faces"}</p>
              <p className="text-sm text-muted-foreground">{loadError.message}</p>
              {loadStale ? (
                <Button onClick={stale}>Refresh the list</Button>
              ) : (
                <Button variant="outline" onClick={() => detail.refetch()}>
                  <RefreshCw /> Retry
                </Button>
              )}
            </div>
          ) : (
            <>
              <div className="flex items-center justify-between text-sm">
                <span aria-live="polite" data-testid="selected-count">
                  {selectedCount} of {total} selected
                </span>
                <div className="flex gap-2">
                  <Button size="sm" variant="ghost" onClick={() => setDeselected(new Set())} disabled={deselected.size === 0}>
                    Select all
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setDeselected(new Set(loadedIds))} disabled={selectedCount === 0}>
                    Select none
                  </Button>
                </div>
              </div>
              <ul className="grid max-h-[50vh] grid-cols-4 gap-2 overflow-y-auto sm:grid-cols-6" aria-label="Faces in this cluster">
                {faces.map((f) => {
                  const on = !deselected.has(f.face_id);
                  return (
                    <li key={f.face_id}>
                      <button
                        type="button"
                        aria-pressed={on}
                        aria-label={`Face ${f.face_id}${f.group_name ? ` from ${f.group_name}` : ""}`}
                        data-testid={`face-${f.face_id}`}
                        onClick={() => toggle(f.face_id)}
                        className={cn(
                          "relative block aspect-square w-full overflow-hidden rounded-md border-2 bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                          on ? "border-primary" : "border-transparent opacity-40",
                        )}
                      >
                        {f.crop_url && <img src={f.crop_url} alt="" loading="lazy" className="h-full w-full object-cover" />}
                        {on && (
                          <span className="absolute right-1 top-1 rounded-full bg-primary p-0.5 text-primary-foreground">
                            <Check className="h-3 w-3" aria-hidden="true" />
                          </span>
                        )}
                      </button>
                    </li>
                  );
                })}
              </ul>
              {more && (
                <Button variant="outline" size="sm" className="justify-self-center" onClick={() => detail.fetchNextPage()} disabled={detail.isFetchingNextPage}>
                  {detail.isFetchingNextPage && <Loader2 className="animate-spin" />} Load more faces
                </Button>
              )}
              <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground">
                This is…
                <select className={selectClass} dir="auto" value={kidId} onChange={(e) => setKidId(e.target.value)} aria-label="Kid">
                  <option value="">Choose a kid</option>
                  {activeKids.map((k) => (
                    <option key={k.id} value={k.id} dir="auto">
                      {k.display_name}
                    </option>
                  ))}
                </select>
              </label>
            </>
          )}

          <DialogFooter className="gap-2 sm:justify-between">
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" onClick={() => split.mutate()} disabled={busy || detail.isPending || !!loadError}>
                {split.isPending ? <Loader2 className="animate-spin" /> : <Scissors />} Split
              </Button>
              <Button variant="destructive" onClick={() => setConfirmIgnore(true)} disabled={busy || detail.isPending || !!loadError}>
                <Trash2 /> Not my kid / ignore
              </Button>
            </div>
            <Button onClick={() => label.mutate()} disabled={!canLabel}>
              {label.isPending ? <Loader2 className="animate-spin" /> : <UserCheck />}
              {kidId ? `This is ${kids.find((k) => String(k.id) === kidId)?.display_name ?? ""}` : "This is…"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={confirmIgnore} onOpenChange={(o) => !o && !ignore.isPending && setConfirmIgnore(false)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Ignore this cluster?</DialogTitle>
            <DialogDescription>
              The {faceCountLabel(total)} crops and embeddings in this cluster are deleted immediately. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmIgnore(false)} disabled={ignore.isPending}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={() => ignore.mutate()} disabled={ignore.isPending}>
              {ignore.isPending && <Loader2 className="animate-spin" />} Delete permanently
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
