import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Camera, ChevronLeft, ChevronRight, Loader2, RefreshCw, Trash2, Users } from "lucide-react";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";
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
import { activityKeys } from "@/lib/activity";
import { formatDateTime } from "@/lib/format";
import { groupKeys, groupLabel, groupsApi } from "@/lib/groups";
import { kidKeys, kidsApi } from "@/lib/kids";
import { useLive } from "@/lib/query";
import {
  badgeKids,
  flattenPages,
  hasActiveFilters,
  invalidRange,
  type PhotoKid,
  type PhotoFilters,
  photoKeys,
  photoKids,
  photosApi,
  groupByMonth,
  mergeTabs,
  UNKNOWN_SENDER,
} from "@/lib/photos";
import { cn } from "@/lib/utils";
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

function isTyping(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  return !!el && (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || el.isContentEditable);
}

export function GalleryPage() {
  usePageTitle("Gallery");
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const [others, setOthers] = useState({ groupId: "", from: "", to: "" });
  const kidParam = params.get("kid") ?? "";
  const filters: PhotoFilters = { kidId: /^\d+$/.test(kidParam) ? kidParam : "", ...others };
  // The lightbox tracks the IMAGE, not a saved_photo row: an "All" row's id changes when its first kid is removed.
  const [openImage, setOpenImage] = useState<number | null>(null);
  const [wantNext, setWantNext] = useState(false);
  const [confirm, setConfirm] = useState<PhotoKid | null>(null);
  const tabKid = filters.kidId === "" ? null : Number(filters.kidId);

  const kids = useLive(kidKeys.list, kidsApi.list);
  const groups = useLive(groupKeys.list, groupsApi.list);
  const bad = invalidRange(filters);

  const counts = useQuery({
    queryKey: photoKeys.counts(filters),
    queryFn: () => photosApi.counts(filters),
    enabled: !bad,
  });
  const photos = useInfiniteQuery({
    queryKey: photoKeys.list(filters),
    queryFn: ({ pageParam }) => photosApi.list(filters, pageParam),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next_cursor,
    enabled: !bad,
  });
  const all = useMemo(() => flattenPages(photos.data?.pages ?? []), [photos.data]);
  const months = useMemo(() => groupByMonth(all), [all]);
  const tabs = useMemo(() => mergeTabs(kids.data ?? [], counts.data), [kids.data, counts.data]);

  const idx = openImage === null ? -1 : all.findIndex((p) => p.image_id === openImage);
  const current = idx >= 0 ? all[idx]! : null;
  const prev = idx > 0 ? all[idx - 1]! : null;
  const next = idx >= 0 && idx < all.length - 1 ? all[idx + 1]! : null;
  const { hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage } = photos;
  const loadingNext = wantNext && isFetchingNextPage;
  const nextDisabled = !next && (!hasNextPage || isFetchingNextPage);

  const goNext = () => {
    if (next) setOpenImage(next.image_id);
    else if (hasNextPage && !isFetchingNextPage) {
      setWantNext(true);
      void fetchNextPage();
    }
  };
  const goPrev = () => prev && setOpenImage(prev.image_id);

  // Navigated past the last loaded photo: continue once the next page has arrived; stop at the real end.
  useEffect(() => {
    if (!wantNext) return;
    if (next) {
      setOpenImage(next.image_id);
      setWantNext(false);
    } else if (!isFetchingNextPage && (!hasNextPage || isFetchNextPageError)) setWantNext(false);
  }, [wantNext, next, isFetchingNextPage, hasNextPage, isFetchNextPageError]);
  useEffect(() => {
    if (!current) setWantNext(false);
  }, [current]);

  // Lightbox arrow keys: ignore auto-repeat, typing targets and the confirm dialog.
  useEffect(() => {
    if (!current || confirm) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.repeat || isTyping(e.target)) return;
      if (e.key === "ArrowLeft") goPrev();
      else if (e.key === "ArrowRight") goNext();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const remove = useMutation({
    mutationFn: (k: PhotoKid) => photosApi.remove(k.saved_photo_id),
    onSuccess: (_r, k) => {
      toast.success(`Removed from ${k.kid_name}`);
      setConfirm(null);
      // Keep the lightbox open while the photo is still listed (other kids remain in this view); else advance.
      const remaining = current ? photoKids(current).filter((x) => x.kid_id !== k.kid_id) : [];
      const stays = tabKid === null ? remaining.length > 0 : k.kid_id !== tabKid;
      if (!stays) setOpenImage(next?.image_id ?? prev?.image_id ?? null);
      void qc.invalidateQueries({ queryKey: photoKeys.all });
      void qc.invalidateQueries({ queryKey: kidKeys.all });
      void qc.invalidateQueries({ queryKey: activityKeys.all });
    },
    onError: (e) => {
      toast.error("Could not remove the photo", { description: e.message });
      setConfirm(null);
      void qc.invalidateQueries({ queryKey: photoKeys.all });
    },
  });

  const set = (patch: Partial<typeof others>) => setOthers((f) => ({ ...f, ...patch }));
  const selectKid = (id: number | null) =>
    setParams(
      (cur) => {
        const n = new URLSearchParams(cur);
        if (id === null) n.delete("kid");
        else n.set("kid", String(id));
        return n;
      },
      { replace: true },
    );
  const clearFilters = () => {
    setOthers({ groupId: "", from: "", to: "" });
    selectKid(null);
  };

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
    const filtered = hasActiveFilters(filters);
    body = (
      <Card className="border-dashed">
        <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-accent text-accent-foreground">
            <Camera className="h-6 w-6" aria-hidden="true" />
          </div>
          <p className="font-medium">{filtered ? "No photos match these filters" : "No photos yet"}</p>
          <p className="max-w-sm text-sm text-muted-foreground">
            {filtered ? (
              "Try another kid, widen the date range or clear a filter."
            ) : (
              <>
                This fills up as Kino recognises your kids in watched groups. Teach it their faces on the{" "}
                <Link className="font-medium underline" to="/references">
                  References
                </Link>{" "}
                page, or{" "}
                <Link className="font-medium underline" to="/kids">
                  enroll a kid
                </Link>
                .
              </>
            )}
          </p>
          {filtered && (
            <Button variant="outline" onClick={clearFilters}>
              Clear filters
            </Button>
          )}
        </CardContent>
      </Card>
    );
  } else {
    body = (
      <>
        <div className="space-y-6">
          {months.map((m) => (
            <section key={m.key} aria-label={m.label}>
              <h2 className="mb-2 text-sm font-semibold text-muted-foreground">{m.label}</h2>
              <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
                {m.items.map((p) => {
                  const kids = photoKids(p);
                  const badges = badgeKids(p, tabKid);
                  return (
                  <li key={p.id}>
                    <button
                      type="button"
                      onClick={() => setOpenImage(p.image_id)}
                      aria-label={`Open photo of ${(tabKid === null ? kids.map((k) => k.kid_name).join(", ") : p.kid_name)}${p.group_name ? ` from ${p.group_name}` : ""}`}
                      className="group relative block aspect-square w-full overflow-hidden rounded-lg border bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      <img
                        src={p.thumb_url}
                        alt=""
                        loading="lazy"
                        decoding="async"
                        className="h-full w-full object-cover transition group-hover:scale-105"
                      />
                      {tabKid === null ? (
                        <span className="absolute inset-x-0 bottom-0 flex flex-wrap gap-1 bg-black/60 p-1">
                          {badges.map((k) => (
                            <span
                              key={k.kid_id}
                              data-testid="kid-badge"
                              dir="auto"
                              className="max-w-full truncate rounded-full bg-primary px-2 py-0.5 text-xs text-primary-foreground"
                            >
                              {k.kid_name}
                            </span>
                          ))}
                        </span>
                      ) : (
                        <>
                          <span dir="auto" className="absolute inset-x-0 bottom-0 truncate bg-black/60 px-2 py-1 text-start text-xs text-white">
                            {p.kid_name}
                          </span>
                          {badges.length > 0 && (
                            <span
                              data-testid="shared-badge"
                              className="absolute end-1 top-1 flex max-w-[90%] items-center gap-1 rounded-full bg-primary px-2 py-0.5 text-xs text-primary-foreground shadow"
                            >
                              <Users className="h-3 w-3 shrink-0" aria-hidden="true" />
                              <span dir="auto" className="truncate">
                                {badges.map((k) => k.kid_name).join(", ")}
                              </span>
                            </span>
                          )}
                        </>
                      )}
                    </button>
                  </li>
                  );
                })}
              </ul>
            </section>
          ))}
        </div>
        <div className="flex justify-center py-6">
          {photos.hasNextPage ? (
            <Button variant="outline" onClick={() => photos.fetchNextPage()} disabled={photos.isFetchingNextPage}>
              {photos.isFetchingNextPage && <Loader2 className="animate-spin" />} Load more
            </Button>
          ) : (
            <p className="text-xs text-muted-foreground">
              {all.length} photo{all.length === 1 ? "" : "s"}
            </p>
          )}
        </div>
      </>
    );
  }

  const currentKids = current ? photoKids(current) : [];
  const together = currentKids.map((k) => k.kid_name);

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader title="Gallery" description="Photos Kino identified and saved for each kid." />
      <div role="tablist" aria-label="Kids" className="mb-4 flex gap-1 overflow-x-auto border-b">
        {tabs.map((t) => {
          const active = (t.kidId === null && filters.kidId === "") || String(t.kidId) === filters.kidId;
          return (
            <button
              key={t.kidId ?? "all"}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => selectKid(t.kidId)}
              className={cn(
                "-mb-px flex shrink-0 items-center gap-2 border-b-2 px-3 py-2 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                active ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              <span dir="auto">{t.name}</span>
              <span className="rounded-full bg-muted px-1.5 text-xs tabular-nums" aria-label={`${t.count} photos`}>
                {t.count}
              </span>
            </button>
          );
        })}
      </div>
      <div className="mb-4 flex flex-wrap items-end gap-3" role="search" aria-label="Photo filters">
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
        {(others.groupId || others.from || others.to) && (
          <Button variant="ghost" onClick={() => setOthers({ groupId: "", from: "", to: "" })}>
            Clear
          </Button>
        )}
      </div>
      {body}

      <Dialog open={current !== null} onOpenChange={(o) => !o && !remove.isPending && !confirm && setOpenImage(null)}>
        <DialogContent className="max-h-[95vh] max-w-4xl overflow-y-auto">
          {current && (
            <>
              <DialogHeader>
                <DialogTitle>
                  Photo of <span dir="auto">{together.join(", ")}</span>
                </DialogTitle>
                <DialogDescription>
                  {idx + 1} of {all.length}{hasNextPage ? "+" : ""}
                </DialogDescription>
              </DialogHeader>
              <div className="relative">
                <img
                  key={current.id}
                  src={current.file_url}
                  alt={`Photo of ${together.join(", ")}`}
                  className="mx-auto max-h-[60vh] w-auto max-w-full rounded-md object-contain"
                />
                <Button
                  variant="secondary"
                  size="icon"
                  aria-label="Previous photo"
                  disabled={!prev}
                  onClick={goPrev}
                  className="absolute start-1 top-1/2 -translate-y-1/2 opacity-90"
                >
                  <ChevronLeft />
                </Button>
                <Button
                  variant="secondary"
                  size="icon"
                  aria-label="Next photo"
                  disabled={nextDisabled || loadingNext}
                  onClick={goNext}
                  className="absolute end-1 top-1/2 -translate-y-1/2 opacity-90"
                >
                  {loadingNext ? <Loader2 className="animate-spin" /> : <ChevronRight />}
                </Button>
              </div>
              <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <Meta label="Kids">
                  <span dir="auto">{together.join(", ")}</span>
                </Meta>
                <Meta label="Group">
                  <span dir="auto">{current.group_name ?? "—"}</span>
                </Meta>
                <Meta label="Sent">{formatDateTime(current.sent_at)}</Meta>
                <Meta label="Sender">
                  {current.sender_name ? <span dir="auto">{current.sender_name}</span> : <span className="text-muted-foreground">{UNKNOWN_SENDER}</span>}
                </Meta>
              </dl>
              <DialogFooter className="flex-wrap gap-2 sm:justify-start">
                {currentKids.map((k) => (
                  <Button key={k.saved_photo_id} variant="destructive" size="sm" onClick={() => setConfirm(k)}>
                    <Trash2 /> Remove from <span dir="auto">{k.kid_name}</span>
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
