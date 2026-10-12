import { AlertCircle, Loader2, ScanFace, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { boxStyle, soleSelectable, type UploadFace, type UploadResult } from "@/lib/enroll";
import { cn } from "@/lib/utils";

export interface Entry {
  key: string;
  name: string;
  url: string;
  status: "uploading" | "done" | "error";
  result?: UploadResult;
  error?: string;
}

interface Props {
  entry: Entry;
  selected: ReadonlySet<string>;
  date: string;
  onToggle: (face: UploadFace) => void;
  onUse: (face: UploadFace) => void;
  onDate: (date: string) => void;
  onRemove: () => void;
}

function FaceBox({
  face,
  index,
  style,
  selected,
  onToggle,
}: {
  face: UploadFace;
  index: number;
  style: React.CSSProperties;
  selected: boolean;
  onToggle: () => void;
}) {
  const label = face.selectable
    ? `Face ${index + 1}, quality ${Math.round(face.quality * 100)}%${selected ? ", selected" : ""}`
    : `Face ${index + 1} cannot be used: ${face.reason ?? "not usable"}`;
  const box = (
    <button
      type="button"
      aria-label={label}
      aria-pressed={face.selectable ? selected : undefined}
      aria-disabled={!face.selectable}
      onClick={() => face.selectable && onToggle()}
      className={cn(
        "absolute rounded-sm border-2 outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring",
        selected && "border-success bg-success/30",
        face.selectable && !selected && "cursor-pointer border-primary bg-primary/10 hover:bg-primary/25",
        !face.selectable && "cursor-not-allowed border-dashed border-destructive/70 bg-destructive/10",
      )}
      style={style}
    />
  );
  if (face.selectable) return box;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{box}</TooltipTrigger>
      <TooltipContent>{face.reason ?? "Not usable"}</TooltipContent>
    </Tooltip>
  );
}

export function PhotoCard({ entry, selected, date, onToggle, onUse, onDate, onRemove }: Props) {
  const r = entry.result;
  const sole = r ? soleSelectable(r) : null;
  const dateId = `date-${entry.key}`;
  return (
    <div className="overflow-hidden rounded-xl border bg-card" data-testid={`photo-${entry.name}`}>
      <div className="flex items-center justify-between gap-2 border-b px-3 py-2">
        <span className="min-w-0 truncate text-sm font-medium" title={entry.name} dir="auto">
          {entry.name}
        </span>
        <Button type="button" size="icon" variant="ghost" className="h-7 w-7" onClick={onRemove} aria-label={`Remove ${entry.name}`}>
          <X />
        </Button>
      </div>

      {entry.status === "uploading" && (
        <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground" role="status">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> Finding faces…
        </div>
      )}

      {entry.status === "error" && (
        <div className="flex items-start gap-2 px-3 py-6 text-sm text-destructive" role="alert">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <span>{entry.error ?? "Upload failed."}</span>
        </div>
      )}

      {entry.status === "done" && r && (
        <>
          <div
            className="relative w-full bg-muted"
            style={{ aspectRatio: r.width && r.height ? `${r.width} / ${r.height}` : "4 / 3" }}
          >
            <img src={entry.url} alt={entry.name} className="absolute inset-0 h-full w-full object-contain" />
            {r.width && r.height &&
              r.faces.map((f, i) => (
                <FaceBox
                  key={f.temp_face_id}
                  face={f}
                  style={boxStyle(f.bbox, r.width!, r.height!)}
                  index={i}
                  selected={selected.has(f.temp_face_id)}
                  onToggle={() => onToggle(f)}
                />
              ))}
          </div>
          <div className="space-y-2 px-3 py-3">
            {r.faces.length === 0 ? (
              <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <ScanFace className="h-4 w-4" aria-hidden="true" /> No faces found in this photo.
              </p>
            ) : (
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-xs text-muted-foreground">
                  {r.faces.length} {r.faces.length === 1 ? "face" : "faces"}. Click a box to pick this kid.
                </p>
                {sole && (
                  <Button
                    type="button"
                    size="sm"
                    variant={selected.has(sole.temp_face_id) ? "secondary" : "default"}
                    onClick={() => onUse(sole)}
                    aria-pressed={selected.has(sole.temp_face_id)}
                  >
                    {selected.has(sole.temp_face_id) ? "Selected" : "Use this face"}
                  </Button>
                )}
              </div>
            )}
            {r.faces.some((f) => f.selectable) && (
              <div className="flex items-center gap-2">
                <label htmlFor={dateId} className="text-xs text-muted-foreground">
                  Photo date{r.photo_date ? " (from EXIF)" : ""}
                </label>
                <Input id={dateId} type="date" value={date} onChange={(e) => onDate(e.target.value)} className="h-8 w-40" />
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
