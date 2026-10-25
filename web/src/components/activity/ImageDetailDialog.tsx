import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import {
  activityApi,
  activityKeys,
  boxStyle,
  formatMs,
  type ImageDetail,
  statusLabel,
  channelLabel,
  statusTone,
} from "@/lib/activity";
import { formatDateTime } from "@/lib/format";

function BoxedImage({ detail }: { detail: ImageDetail }) {
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  if (!detail.file_url) {
    return (
      <div className="flex aspect-[4/3] items-center justify-center rounded-lg border border-dashed bg-muted text-sm text-muted-foreground">
        Original no longer stored
      </div>
    );
  }
  return (
    <div className="relative mx-auto inline-block max-w-full overflow-hidden rounded-lg border bg-muted leading-none">
      <img
        src={detail.file_url}
        alt="Photo with detected faces"
        className="block max-h-[50vh] w-auto max-w-full"
        onLoad={(e) => setSize({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })}
      />
      {size &&
        size.w > 0 &&
        size.h > 0 &&
        detail.faces.map((f, i) => (
          <div
            key={f.id}
            data-testid="face-box"
            aria-hidden="true"
            className="pointer-events-none absolute rounded-sm border-2 border-primary shadow-[0_0_0_1px_rgba(255,255,255,0.8)]"
            style={boxStyle(f.bbox, size)}
          >
            <span className="absolute -top-0.5 left-0 rounded-sm bg-primary px-1 text-[10px] leading-4 text-primary-foreground">
              {i + 1}
            </span>
          </div>
        ))}
    </div>
  );
}

export function ImageDetailDialog({ imageId, onClose }: { imageId: number | null; onClose: () => void }) {
  const q = useQuery({
    queryKey: activityKeys.detail(imageId ?? 0),
    queryFn: () => activityApi.detail(imageId as number),
    enabled: imageId !== null,
  });
  const d = q.data;
  const kidName = (id: number) => d?.matches.find((m) => m.kid_id === id)?.kid_name ?? `Kid ${id}`;

  return (
    <Dialog open={imageId !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[95vh] max-w-4xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Image #{imageId}</DialogTitle>
          <DialogDescription>Pipeline outcome, detected faces and forwards.</DialogDescription>
        </DialogHeader>
        {q.isPending && (
          <div className="space-y-3" aria-busy="true" aria-label="Loading image">
            <Skeleton className="h-64 w-full" />
            <Skeleton className="h-16 w-full" />
          </div>
        )}
        {q.isError && (
          <div className="flex flex-col items-center gap-3 py-8 text-center">
            <p className="font-medium">Could not load this image</p>
            <p className="text-sm text-muted-foreground">{q.error.message}</p>
            <Button variant="outline" onClick={() => q.refetch()}>
              <RefreshCw /> Retry
            </Button>
          </div>
        )}
        {d && (
          <div className="space-y-4">
            <BoxedImage detail={d} />
            <dl className="grid gap-3 sm:grid-cols-4">
              <div>
                <dt className="text-xs text-muted-foreground">Status</dt>
                <dd>
                  <Badge variant={statusTone(d.status)}>{statusLabel(d.status, d.skip_reason)}</Badge>
                </dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">Group</dt>
                <dd dir="auto" className="text-sm">{d.group_name ?? "—"}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">Sender</dt>
                <dd dir="auto" className="text-sm">{d.sender ?? "—"}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">Received</dt>
                <dd className="text-sm">{formatDateTime(d.received_at)}</dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">Processing time</dt>
                <dd className="text-sm">{formatMs(d.processing_ms)}</dd>
              </div>
              {d.width && d.height && (
                <div>
                  <dt className="text-xs text-muted-foreground">Size</dt>
                  <dd className="text-sm">{d.width} × {d.height}</dd>
                </div>
              )}
              {d.duplicate_of !== null && (
                <div>
                  <dt className="text-xs text-muted-foreground">Duplicate of</dt>
                  <dd className="text-sm">#{d.duplicate_of}</dd>
                </div>
              )}
            </dl>
            {d.error && (
              <p role="alert" className="whitespace-pre-wrap break-words rounded-md border border-destructive/50 p-3 text-sm text-destructive">
                {d.error}
              </p>
            )}

            <section aria-label="Faces">
              <h3 className="mb-2 text-sm font-semibold">Faces ({d.faces.length})</h3>
              {d.faces.length === 0 ? (
                <p className="text-sm text-muted-foreground">No faces detected.</p>
              ) : (
                <ul className="space-y-2">
                  {d.faces.map((f, i) => {
                    const ms = d.matches.filter((m) => m.face_id === f.id);
                    return (
                      <li key={f.id} className="flex flex-wrap items-center gap-3 rounded-md border p-2 text-sm">
                        <span className="w-5 text-center font-medium">{i + 1}</span>
                        {f.crop_url && <img src={f.crop_url} alt="" className="h-10 w-10 rounded object-cover" />}
                        <Badge variant="outline">tier {f.tier}</Badge>
                        <span className="text-muted-foreground">
                          {Math.round(f.size_px)} px · quality {f.quality === null ? "—" : f.quality.toFixed(2)}
                          {f.from_tile ? " · tile" : ""}
                        </span>
                        <span className="ms-auto flex flex-wrap gap-2">
                          {ms.length === 0 ? (
                            <span className="text-muted-foreground">no match</span>
                          ) : (
                            ms.map((m) => (
                              <span key={m.id} className="flex items-center gap-1">
                                <span dir="auto" className="font-medium">{m.kid_name}</span>
                                <span className="text-muted-foreground">{m.score.toFixed(2)}</span>
                                <Badge variant={m.decision === "rejected" || m.decision === "dropped" ? "outline" : "secondary"}>
                                  {m.decision.replace(/_/g, " ")}
                                </Badge>
                              </span>
                            ))
                          )}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>

            <section aria-label="Forwards">
              <h3 className="mb-2 text-sm font-semibold">Forwards ({d.forwards.length})</h3>
              {d.forwards.length === 0 ? (
                <p className="text-sm text-muted-foreground">Not forwarded.</p>
              ) : (
                <ul className="space-y-2">
                  {d.forwards.map((f) => (
                    <li key={f.id} className="rounded-md border p-2 text-sm">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant={statusTone(f.status)}>{f.status}</Badge>
                        <Badge variant="outline" data-testid="forward-channel">{channelLabel(f.channel)}</Badge>
                        <span dir="auto">{f.kid_ids.map(kidName).join(", ")}</span>
                        <span className="text-muted-foreground">
                          {f.attempts} attempt{f.attempts === 1 ? "" : "s"} · {formatDateTime(f.sent_at ?? f.created_at)}
                        </span>
                      </div>
                      {f.last_error && (
                        <p className="mt-1 whitespace-pre-wrap break-words text-xs text-destructive">{f.last_error}</p>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
