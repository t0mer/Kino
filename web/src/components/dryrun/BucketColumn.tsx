import { ImageOff } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { type BucketName, cappedNote, type ScoreBucket, type ScoreFace } from "@/lib/dryrun";

export const BUCKET_META: Record<BucketName, { title: string; hint: string }> = {
  would_auto_save: { title: "Would auto-save", hint: "Saved and forwarded without asking" },
  would_review: { title: "Would review", hint: "Waits in the review queue" },
  would_drop: { title: "Would drop", hint: "Ignored" },
};

export function BucketColumn({
  name,
  bucket,
  onPick,
}: {
  name: BucketName;
  bucket: ScoreBucket;
  onPick: (f: ScoreFace) => void;
}) {
  const meta = BUCKET_META[name];
  const note = cappedNote(bucket);
  return (
    <section aria-label={meta.title} className="flex min-h-[12rem] flex-col rounded-lg border bg-card">
      <header className="border-b px-3 py-2">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">{meta.title}</h2>
          <Badge variant="secondary" aria-label={`${bucket.count} faces`}>
            {bucket.count}
          </Badge>
        </div>
        <p className="text-xs text-muted-foreground">{meta.hint}</p>
      </header>
      <div className="flex-1 p-3">
        {bucket.items.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">Nothing here.</p>
        ) : (
          <ul className="grid grid-cols-[repeat(auto-fill,minmax(4.5rem,1fr))] gap-2">
            {bucket.items.map((f) => (
              <li key={f.face_id}>
                <button
                  type="button"
                  onClick={() => onPick(f)}
                  aria-label={`Face ${f.face_id}, score ${f.score.toFixed(2)}${f.kid_name ? `, ${f.kid_name}` : ""}`}
                  className="group block w-full overflow-hidden rounded-md border bg-muted text-start focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <span className="flex aspect-square items-center justify-center">
                    {f.crop_url ? (
                      <img src={f.crop_url} alt="" loading="lazy" className="h-full w-full object-cover" />
                    ) : (
                      <ImageOff className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
                    )}
                  </span>
                  <span className="block px-1 py-0.5 text-[11px] tabular-nums text-muted-foreground group-hover:text-foreground">
                    {f.score.toFixed(2)} · {f.tier}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      {note && <footer className="border-t px-3 py-1.5 text-xs text-muted-foreground">{note}</footer>}
    </section>
  );
}
