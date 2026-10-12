import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Check, Loader2 } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { enrollApi, imageKeys } from "@/lib/enroll";
import { groupKeys, groupLabel, kidsApi } from "@/lib/kids";
import { cn } from "@/lib/utils";

const ALL = "all";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called with the chosen image ids; the page fetches and uploads them. */
  onPick: (ids: number[]) => void;
}

export function ReceivedPhotosDialog({ open, onOpenChange, onPick }: Props) {
  const [group, setGroup] = useState<string>(ALL);
  const [chosen, setChosen] = useState<Set<number>>(new Set());
  const groupId = group === ALL ? null : Number(group);

  const groups = useQuery({ queryKey: groupKeys.list, queryFn: kidsApi.groups, enabled: open });
  const images = useInfiniteQuery({
    queryKey: imageKeys.list(groupId),
    queryFn: ({ pageParam }) => enrollApi.images(groupId, pageParam),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next_cursor,
    enabled: open,
  });
  const items = (images.data?.pages.flatMap((p) => p.items) ?? []).filter((i) => i.has_file);

  const toggle = (id: number) =>
    setChosen((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  const close = (o: boolean) => {
    if (!o) setChosen(new Set());
    onOpenChange(o);
  };

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Pick from received photos</DialogTitle>
          <DialogDescription>Choose photos Kino already received. Faces are found again for enrollment.</DialogDescription>
        </DialogHeader>

        <div className="flex items-center gap-2">
          <label htmlFor="recv-group" className="text-sm font-medium">
            Group
          </label>
          <Select value={group} onValueChange={setGroup}>
            <SelectTrigger id="recv-group" className="w-64">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All groups</SelectItem>
              {groups.data?.map((g) => (
                <SelectItem key={g.id} value={String(g.id)}>
                  <span dir="auto">{groupLabel(g)}</span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {images.isPending ? (
          <div className="grid grid-cols-3 gap-2 sm:grid-cols-4" aria-busy="true">
            {Array.from({ length: 8 }, (_, i) => (
              <Skeleton key={i} className="aspect-square rounded-lg" />
            ))}
          </div>
        ) : images.isError ? (
          <div className="py-8 text-center text-sm">
            <p className="text-muted-foreground">{images.error.message}</p>
            <Button variant="outline" className="mt-3" onClick={() => images.refetch()}>
              Retry
            </Button>
          </div>
        ) : items.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">No received photos here yet.</p>
        ) : (
          <>
            <ul className="grid grid-cols-3 gap-2 sm:grid-cols-4">
              {items.map((i) => {
                const on = chosen.has(i.id);
                return (
                  <li key={i.id}>
                    <button
                      type="button"
                      aria-pressed={on}
                      aria-label={`Photo ${i.id}${i.group_name ? ` from ${i.group_name}` : ""}, ${i.face_count} faces`}
                      onClick={() => toggle(i.id)}
                      className={cn(
                        "relative block aspect-square w-full overflow-hidden rounded-lg border-2 bg-muted outline-none focus-visible:ring-2 focus-visible:ring-ring",
                        on ? "border-primary" : "border-transparent",
                      )}
                    >
                      <img src={`/api/images/${i.id}/file`} alt="" loading="lazy" className="h-full w-full object-cover" />
                      {on && (
                        <span className="absolute right-1 top-1 rounded-full bg-primary p-0.5 text-primary-foreground">
                          <Check className="h-3.5 w-3.5" aria-hidden="true" />
                        </span>
                      )}
                      <span className="absolute bottom-1 left-1 rounded bg-background/80 px-1 text-[10px] tabular-nums">
                        {i.face_count} {i.face_count === 1 ? "face" : "faces"}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
            {images.hasNextPage && (
              <Button variant="outline" onClick={() => images.fetchNextPage()} disabled={images.isFetchingNextPage}>
                {images.isFetchingNextPage && <Loader2 className="animate-spin" />} Load more
              </Button>
            )}
          </>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => close(false)}>
            Cancel
          </Button>
          <Button
            disabled={chosen.size === 0}
            onClick={() => {
              onPick([...chosen]);
              close(false);
            }}
          >
            Add {chosen.size || ""} {chosen.size === 1 ? "photo" : "photos"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
