import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Camera, Loader2, RefreshCw, Trash2 } from "lucide-react";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
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
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/sonner";
import { formatDateTime } from "@/lib/format";
import { groupKeys, groupLabel, groupsApi } from "@/lib/groups";
import { kidKeys, kidsApi } from "@/lib/kids";
import { useLive } from "@/lib/query";
import {
  EMPTY_FILTERS,
  flattenPages,
  hasActiveFilters,
  invalidRange,
  type Photo,
  type PhotoFilters,
  photoKeys,
  photosApi,
  siblingsOf,
} from "@/lib/photos";
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

function Meta({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm">{children}</dd>
    </div>
  );
}

export function PhotosPage() {
  usePageTitle("Photos");
  const qc = useQueryClient();
  const [filters, setFilters] = useState<PhotoFilters>(EMPTY_FILTERS);
  const [open, setOpen] = useState<Photo | null>(null);
  const [confirm, setConfirm] = useState<Photo | null>(null);

  const kids = useLive(kidKeys.list, kidsApi.list);
  const groups = useLive(groupKeys.list, groupsApi.list);
  const bad = invalidRange(filters);

  const photos = useInfiniteQuery({
    queryKey: photoKeys.list(filters),
    queryFn: ({ pageParam }) => photosApi.list(filters, pageParam),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next_cursor,
    enabled: !bad,
  });
  const all = useMemo(() => flattenPages(photos.data?.pages ?? []), [photos.data]);

  // Infinite scroll on top of the explicit "Load more" button.
  const sentinel = useRef<HTMLDivElement>(null);
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = photos;
  useEffect(() => {
    const el = sentinel.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting) && hasNextPage && !isFetchingNextPage) void fetchNextPage();
    });
    io.observe(el);
    return () => io.disconnect();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);

  const remove = useMutation({
    mutationFn: (p: Photo) => photosApi.remove(p.id),
    onSuccess: (_r, p) => {
      toast.success(`Removed from ${p.kid_name}`);
      setConfirm(null);
      // Close the lightbox unless a sibling row of the same image is still saved.
      setOpen((cur) => (cur && siblingsOf(cur, all).some((s) => s.id !== p.id) ? cur : null));
      void qc.invalidateQueries({ queryKey: photoKeys.all });
      void qc.invalidateQueries({ queryKey: kidKeys.list });
    },
    onError: (e) => {
      toast.error("Could not remove the photo", { description: e.message });
      setConfirm(null);
      void qc.invalidateQueries({ queryKey: photoKeys.all });
    },
  });

  const set = (patch: Partial<PhotoFilters>) => setFilters((f) => ({ ...f, ...patch }));
  const siblings = open ? siblingsOf(open, all) : [];
  // The lightbox row may vanish after a refetch (removed elsewhere); fall back to closed.
  const current = open && siblings.length > 0 ? open : null;

  let body: ReactNode;
  if (bad) {
    body = <p className="py-12 text-center text-sm text-muted-foreground">The "from" date is after the "to" date.</p>;
  } else if (photos.isPending) {
    body = (
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5" aria-busy="true" aria-label="Loading photos">
        {Array.from({ length: 10 }, (_, i) => (
          <Skeleton key={i} className="aspect-square rounded-lg" />
        ))}
      </div>
    );
  } else if (photos.isError) {
    body = (
      <Card>
        <CardContent className="flex flex-col items-center gap-3 py-12 text-center">
          <p className="font-medium">Could not load photos</p>
          <p className="text-sm text-muted-foreground">{photos.error.message}</p>
          <Button variant="outline" onClick={() => photos.refetch()}>
            <RefreshCw /> Retry
          </Button>
        </CardContent>
      </Card>
    );
  } else if (all.length === 0) {
    body = (
      <Card className="border-dashed">
        <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-accent text-accent-foreground">
            <Camera className="h-6 w-6" aria-hidden="true" />
          </div>
          <p className="font-medium">{hasActiveFilters(filters) ? "No photos match these filters" : "No saved photos yet"}</p>
          <p className="max-w-sm text-sm text-muted-foreground">
            {hasActiveFilters(filters)
              ? "Try widening the date range or clearing a filter."
              : "Photos appear here once Kino saves a match for one of your kids."}
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
        <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          {all.map((p) => (
            <li key={p.id}>
              <button
                type="button"
                onClick={() => setOpen(p)}
                aria-label={`Open photo of ${p.kid_name}${p.group_name ? ` from ${p.group_name}` : ""}`}
                className="group relative block aspect-square w-full overflow-hidden rounded-lg border bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <img src={p.file_url} alt="" loading="lazy" className="h-full w-full object-cover transition group-hover:scale-105" />
                <span
                  dir="auto"
                  className="absolute inset-x-0 bottom-0 truncate bg-black/60 px-2 py-1 text-start text-xs text-white"
                >
                  {p.kid_name}
                </span>
              </button>
            </li>
          ))}
        </ul>
        <div ref={sentinel} className="flex justify-center py-6">
          {photos.hasNextPage ? (
            <Button variant="outline" onClick={() => photos.fetchNextPage()} disabled={photos.isFetchingNextPage}>
              {photos.isFetchingNextPage && <Loader2 className="animate-spin" />} Load more
            </Button>
          ) : (
            <p className="text-xs text-muted-foreground">{all.length} photo{all.length === 1 ? "" : "s"}</p>
          )}
        </div>
      </>
    );
  }

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader title="Photos" description="Browse the photos saved for each kid." />
      <div className="mb-4 flex flex-wrap items-end gap-3" role="search" aria-label="Photo filters">
        <Field label="Kid">
          <select className={selectClass} value={filters.kidId} onChange={(e) => set({ kidId: e.target.value })}>
            <option value="">All kids</option>
            {kids.data?.map((k) => (
              <option key={k.id} value={k.id} dir="auto">
                {k.display_name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Group">
          <select className={selectClass} value={filters.groupId} onChange={(e) => set({ groupId: e.target.value })}>
            <option value="">All groups</option>
            {groups.data?.map((g) => (
              <option key={g.id} value={g.id} dir="auto">
                {groupLabel(g)}
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
        {hasActiveFilters(filters) && (
          <Button variant="ghost" onClick={() => setFilters(EMPTY_FILTERS)}>
            Clear
          </Button>
        )}
      </div>
      {body}

      <Dialog open={current !== null} onOpenChange={(o) => !o && !remove.isPending && !confirm && setOpen(null)}>
        <DialogContent className="max-h-[95vh] max-w-4xl overflow-y-auto">
          {current && (
            <>
              <DialogHeader>
                <DialogTitle>
                  Photo of <span dir="auto">{siblings.map((s) => s.kid_name).join(", ")}</span>
                </DialogTitle>
                <DialogDescription>Saved original</DialogDescription>
              </DialogHeader>
              <img
                src={current.file_url}
                alt={`Photo of ${siblings.map((s) => s.kid_name).join(", ")}`}
                className="mx-auto max-h-[60vh] w-auto max-w-full rounded-md object-contain"
              />
              <dl className="grid gap-3 sm:grid-cols-3">
                <Meta label="Kids">
                  <span dir="auto">{siblings.map((s) => s.kid_name).join(", ")}</span>
                </Meta>
                <Meta label="Group">
                  <span dir="auto">{current.group_name ?? "—"}</span>
                </Meta>
                <Meta label="Sent">{formatDateTime(current.sent_at)}</Meta>
                <Meta label="Saved">{formatDateTime(current.saved_at)}</Meta>
              </dl>
              <DialogFooter className="flex-wrap gap-2 sm:justify-start">
                {siblings.map((s) => (
                  <Button key={s.id} variant="destructive" size="sm" onClick={() => setConfirm(s)}>
                    <Trash2 /> Remove from <span dir="auto">{s.kid_name}</span>
                  </Button>
                ))}
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>

      <Dialog open={confirm !== null} onOpenChange={(o) => !o && !remove.isPending && setConfirm(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              Remove from <span dir="auto">{confirm?.kid_name}</span>?
            </DialogTitle>
            <DialogDescription>
              The saved file is deleted from this kid&apos;s folder and its pending forward is cancelled. A copy already
              sent to WhatsApp stays there.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirm(null)} disabled={remove.isPending}>
              Cancel
            </Button>
            <Button variant="destructive" disabled={remove.isPending || !confirm} onClick={() => confirm && remove.mutate(confirm)}>
              {remove.isPending && <Loader2 className="animate-spin" />} Remove photo
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
