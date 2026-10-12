import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toast } from "@/components/ui/sonner";
import { isApiError } from "@/lib/api";
import { type Kid, kidKeys, kidsApi } from "@/lib/kids";

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

export function DeleteKidDialog({ kid, onClose }: { kid: Kid | null; onClose: () => void }) {
  const qc = useQueryClient();
  const del = useMutation({
    mutationFn: (k: Kid) => kidsApi.remove(k.id),
    onSuccess: (res, k) => {
      // Drop the card and its per-kid queries first, so nothing refetches a deleted kid (404).
      qc.setQueryData<Kid[]>(kidKeys.list, (list) => list?.filter((x) => x.id !== k.id));
      qc.removeQueries({ queryKey: ["kids", k.id] });
      void qc.invalidateQueries({ queryKey: kidKeys.list });
      void qc.invalidateQueries({ queryKey: ["groups"] });
      void qc.invalidateQueries({ queryKey: ["review"] });
      toast.success(`Deleted ${k.display_name}`, {
        description: `${plural(res.removed_files, "photo file")} removed, ${plural(res.cancelled_forwards, "forward")} cancelled.`,
      });
      onClose();
    },
    onError: (e) => {
      if (isApiError(e, 404)) {
        void qc.invalidateQueries({ queryKey: kidKeys.all });
        onClose();
      }
      toast.error("Could not delete the kid", { description: e.message });
    },
  });

  return (
    <Dialog open={kid !== null} onOpenChange={(o) => !o && !del.isPending && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            Delete <span dir="auto">{kid?.display_name}</span>?
          </DialogTitle>
          <DialogDescription>This cannot be undone.</DialogDescription>
        </DialogHeader>
        <ul className="list-disc space-y-1 ps-5 text-sm">
          <li>All reference faces, negatives and match history for this kid are removed.</li>
          <li>
            Saved photo files in this kid&apos;s folder are deleted (a photo shared with a sibling stays in the
            sibling&apos;s folder).
          </li>
          <li>Pending forwards that only name this kid are cancelled. Messages already sent stay in WhatsApp.</li>
          <li>Clusters labelled as this kid become unlabelled again.</li>
        </ul>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={del.isPending}>
            Cancel
          </Button>
          <Button variant="destructive" disabled={del.isPending || !kid} onClick={() => kid && del.mutate(kid)}>
            {del.isPending && <Loader2 className="animate-spin" />}
            Delete kid
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
