import { Check, HelpCircle, SkipForward, UserX, X } from "lucide-react";
import { FaceBoxImage } from "@/components/review/FaceBoxImage";
import { ItemMeta } from "@/components/review/ItemMeta";
import { Button } from "@/components/ui/button";
import type { Kid } from "@/lib/kids";
import { MAX_NUMBERED_KIDS, formatScore, type ReviewItem } from "@/lib/review";

interface Props {
  item: ReviewItem;
  kids: Kid[];
  busy: boolean;
  onConfirm: () => void;
  onReject: () => void;
  onSomeoneElse: () => void;
  onReassign: (kidId: number) => void;
  onSkip: () => void;
}

function Kbd({ children }: { children: string }) {
  return (
    <kbd className="ms-1 rounded border bg-muted px-1 font-mono text-[10px] text-muted-foreground">{children}</kbd>
  );
}

/** One review item: large crop, photo with the face box, nearest references and all actions. */
export function ReviewCard({ item, kids, busy, onConfirm, onReject, onSomeoneElse, onReassign, onSkip }: Props) {
  const numbered = kids.slice(0, MAX_NUMBERED_KIDS);
  return (
    <div className="space-y-5" data-testid="review-item" data-match-id={item.match_id}>
      <div className="grid gap-5 md:grid-cols-[minmax(12rem,18rem)_1fr]">
        <div className="space-y-3">
          <div className="flex aspect-square w-full max-w-72 items-center justify-center overflow-hidden rounded-xl border bg-muted">
            {item.crop_url ? (
              <img key={item.face_id} src={item.crop_url} alt="Face to review" className="h-full w-full object-cover" />
            ) : (
              <HelpCircle className="h-10 w-10 text-muted-foreground" aria-hidden="true" />
            )}
          </div>
          <p className="text-sm">
            Looks like <strong dir="auto">{item.kid_name}</strong>
            <span className="text-muted-foreground"> ({formatScore(item.score)})</span>
          </p>
          <ItemMeta item={item} />
        </div>
        <div className="min-w-0">
          <FaceBoxImage item={item} />
        </div>
      </div>

      <section aria-label="Nearest reference faces">
        <h2 className="mb-2 text-sm font-medium">
          Closest references of <span dir="auto">{item.kid_name}</span>
        </h2>
        {item.nearest_refs.length === 0 ? (
          <p className="text-sm text-muted-foreground">No reference faces to compare with.</p>
        ) : (
          <ul className="flex flex-wrap gap-3">
            {item.nearest_refs.map((r) => (
              <li key={r.reference_id} className="w-24 text-center">
                <div className="flex aspect-square items-center justify-center overflow-hidden rounded-lg border bg-muted">
                  {r.crop_url ? (
                    <img src={r.crop_url} alt={`Reference ${r.reference_id}`} className="h-full w-full object-cover" />
                  ) : (
                    <HelpCircle className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
                  )}
                </div>
                <span className="text-xs tabular-nums text-muted-foreground">{formatScore(r.similarity)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <div className="flex flex-wrap gap-2">
        <Button onClick={onConfirm} disabled={busy} aria-keyshortcuts="Y">
          <Check /> Confirm <Kbd>Y</Kbd>
        </Button>
        <Button variant="outline" onClick={onReject} disabled={busy} aria-keyshortcuts="N">
          <X /> Reject <Kbd>N</Kbd>
        </Button>
        <Button variant="outline" onClick={onSomeoneElse} disabled={busy} aria-keyshortcuts="O">
          <UserX /> It's someone else <Kbd>O</Kbd>
        </Button>
        <Button variant="ghost" onClick={onSkip} disabled={busy} aria-keyshortcuts="S">
          <SkipForward /> Skip <Kbd>S</Kbd>
        </Button>
      </div>

      {numbered.length > 0 && (
        <section aria-label="Reassign to another kid">
          <h2 className="mb-2 text-sm font-medium">It's actually…</h2>
          <div className="flex flex-wrap gap-2">
            {numbered.map((k, n) => (
              <Button
                key={k.id}
                size="sm"
                variant="secondary"
                disabled={busy || k.id === item.kid_id}
                onClick={() => onReassign(k.id)}
                aria-keyshortcuts={String(n + 1)}
              >
                <span dir="auto">{k.display_name}</span>
                <Kbd>{String(n + 1)}</Kbd>
              </Button>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
