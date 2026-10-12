import { useMutation } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { toast } from "@/components/ui/sonner";
import { isApiError } from "@/lib/api";
import { type BucketName, dryrunApi, type FlagAction, type ScoreFace } from "@/lib/dryrun";
import { formatDateTime } from "@/lib/format";

export function FlagDialog({
  face,
  bucket,
  onClose,
  onDone,
}: {
  face: ScoreFace | null;
  bucket: BucketName | null;
  onClose: () => void;
  onDone: () => void;
}) {
  const flag = useMutation({
    mutationFn: ({ f, action }: { f: ScoreFace; action: FlagAction }) => dryrunApi.flag(f.face_id, f.kid_id!, action),
    onSuccess: (r, { action }) => {
      toast.success(
        !r.changed
          ? "Already flagged."
          : action === "review"
            ? "Sent to the review queue."
            : "Marked as a negative example.",
      );
      onDone();
    },
    onError: (e) => toast.error(isApiError(e) ? e.detail : "Could not flag the face."),
  });
  const kid = face?.kid_name;
  const canFlag = face?.kid_id != null;
  return (
    <Dialog open={face !== null} onOpenChange={(o) => !o && !flag.isPending && onClose()}>
      <DialogContent className="max-w-sm">
        {face && (
          <>
            <DialogHeader>
              <DialogTitle>Flag this face as wrong</DialogTitle>
              <DialogDescription>
                Score {face.score.toFixed(2)} · tier {face.tier}
                {kid && (
                  <>
                    {" "}
                    · best match <span dir="auto">{kid}</span>
                  </>
                )}
              </DialogDescription>
            </DialogHeader>
            <div className="flex gap-3">
              {face.crop_url && <img src={face.crop_url} alt="Face crop" className="h-24 w-24 rounded-md border object-cover" />}
              <dl className="min-w-0 space-y-1 text-sm">
                <div>
                  <dt className="text-xs text-muted-foreground">Group</dt>
                  <dd dir="auto" className="truncate">{face.group_name ?? "—"}</dd>
                </div>
                <div>
                  <dt className="text-xs text-muted-foreground">Sent</dt>
                  <dd>{formatDateTime(face.sent_at)}</dd>
                </div>
              </dl>
            </div>
            {!canFlag && (
              <p className="text-sm text-muted-foreground">No kid was suggested for this face, so there is nothing to flag.</p>
            )}
            <DialogFooter className="flex-col gap-2 sm:flex-col sm:space-x-0">
              {bucket !== "would_review" && (
                <Button
                  variant="outline"
                  disabled={!canFlag || flag.isPending}
                  onClick={() => flag.mutate({ f: face, action: "review" })}
                >
                  {flag.isPending && <Loader2 className="animate-spin" />} Send to review
                </Button>
              )}
              <Button
                variant="outline"
                disabled={!canFlag || flag.isPending}
                onClick={() => flag.mutate({ f: face, action: "negative" })}
              >
                Not <span dir="auto">{kid ?? "this kid"}</span> (negative example)
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
