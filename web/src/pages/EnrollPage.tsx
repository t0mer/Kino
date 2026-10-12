import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Images, Loader2, RefreshCw, Save, Upload } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { type Entry, PhotoCard } from "@/components/enroll/PhotoCard";
import { ReceivedPhotosDialog } from "@/components/enroll/ReceivedPhotosDialog";
import { CoverageMeter } from "@/components/kids/KidCard";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/sonner";
import { isApiError } from "@/lib/api";
import {
  ACCEPT,
  checkFile,
  dateToApi,
  defaultPhotoDate,
  enrollApi,
  groupSelectionByDate,
  pruneSelection,
  toggleFace,
  todayISO,
} from "@/lib/enroll";
import { kidKeys, kidsApi } from "@/lib/kids";
import { usePageTitle } from "@/lib/usePageTitle";
import { cn } from "@/lib/utils";

let counter = 0;

export function EnrollPage() {
  const { id } = useParams();
  const kidId = Number(id);
  const qc = useQueryClient();
  const kid = useQuery({
    queryKey: kidKeys.detail(kidId),
    queryFn: () => kidsApi.get(kidId),
    enabled: Number.isInteger(kidId),
  });
  usePageTitle(kid.data ? `Enroll ${kid.data.display_name}` : "Enroll faces");

  const [entries, setEntries] = useState<Entry[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [dates, setDates] = useState<Record<string, string>>({});
  const [dragging, setDragging] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const urls = useRef<Set<string>>(new Set());

  useEffect(() => {
    const set = urls.current;
    return () => {
      for (const u of set) URL.revokeObjectURL(u);
    };
  }, []);

  const patch = (key: string, p: Partial<Entry>) =>
    setEntries((es) => es.map((e) => (e.key === key ? { ...e, ...p } : e)));

  const addFiles = useCallback(
    async (files: File[]) => {
      const fresh = files.map((f) => {
        const url = URL.createObjectURL(f);
        urls.current.add(url);
        const problem = checkFile(f);
        const entry: Entry = {
          key: `e${++counter}`,
          name: f.name || "photo",
          url,
          status: problem ? "error" : "uploading",
          error: problem ?? undefined,
        };
        return { file: f, entry };
      });
      setEntries((es) => [...es, ...fresh.map((x) => x.entry)]);
      for (const { file, entry } of fresh) {
        if (entry.status === "error") continue;
        try {
          const [result] = await enrollApi.upload(kidId, file);
          if (!result) throw new Error("The server returned no result.");
          if (result.error) {
            patch(entry.key, { status: "error", error: result.error });
          } else {
            patch(entry.key, { status: "done", result });
            setDates((d) => ({ ...d, [result.upload_id]: defaultPhotoDate(result) }));
          }
        } catch (e) {
          patch(entry.key, { status: "error", error: e instanceof Error ? e.message : "Upload failed." });
        }
      }
    },
    [kidId],
  );

  const addReceived = useCallback(
    async (ids: number[]) => {
      const files: File[] = [];
      for (const imageId of ids) {
        try {
          const blob = await enrollApi.imageBlob(imageId);
          files.push(new File([blob], `received-${imageId}.jpg`, { type: blob.type || "image/jpeg" }));
        } catch (e) {
          toast.error(e instanceof Error ? e.message : `Could not load photo ${imageId}`);
        }
      }
      if (files.length) await addFiles(files);
    },
    [addFiles],
  );

  const remove = (key: string) => {
    const next = entries.filter((e) => e.key !== key);
    const gone = entries.find((e) => e.key === key);
    if (gone) {
      URL.revokeObjectURL(gone.url);
      urls.current.delete(gone.url);
    }
    setEntries(next);
    setSelected((s) => pruneSelection(s, next.flatMap((e) => (e.result ? [e.result] : []))));
  };

  const results = entries.flatMap((e) => (e.result ? [e.result] : []));
  const uploading = entries.some((e) => e.status === "uploading");

  const save = useMutation({
    mutationFn: async () => {
      const groups = groupSelectionByDate(results, selected, dates, todayISO());
      const saved = new Set<string>();
      let duplicates = 0;
      let created = 0;
      try {
        for (const g of groups) {
          const res = await enrollApi.addReferences(kidId, g.tempFaceIds, dateToApi(g.date));
          g.tempFaceIds.forEach((t) => saved.add(t));
          created += res.created.length;
          duplicates += res.duplicates;
        }
      } catch (e) {
        throw Object.assign(e instanceof Error ? e : new Error("Save failed"), { saved });
      }
      return { saved, created, duplicates };
    },
    onSuccess: ({ saved, created, duplicates }) => {
      afterSave(saved);
      toast.success(
        `Added ${created} reference${created === 1 ? "" : "s"}${duplicates ? ` (${duplicates} already known)` : ""}`,
      );
    },
    onError: (e: Error & { saved?: Set<string> }) => {
      if (e.saved?.size) afterSave(e.saved);
      toast.error("Could not save the references", {
        description: isApiError(e, 404) ? "The detected faces expired. Add the photos again." : e.message,
      });
    },
    onSettled: () => qc.invalidateQueries({ queryKey: kidKeys.all }),
  });

  function afterSave(saved: Set<string>) {
    setEntries((es) => {
      const kept = es.filter((e) => !e.result?.faces.some((f) => saved.has(f.temp_face_id)));
      for (const e of es) if (!kept.includes(e)) URL.revokeObjectURL(e.url);
      return kept;
    });
    setSelected((s) => new Set([...s].filter((t) => !saved.has(t))));
  }

  if (!Number.isInteger(kidId) || (kid.isError && isApiError(kid.error, 404))) {
    return (
      <div className="mx-auto max-w-3xl">
        <Card className="border-dashed">
          <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
            <p className="font-medium">Kid not found</p>
            <p className="text-sm text-muted-foreground">This kid does not exist or was deleted.</p>
            <Button asChild variant="outline">
              <Link to="/kids">
                <ArrowLeft /> Back to kids
              </Link>
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  const name = kid.data?.display_name;
  const onDrop = (ev: React.DragEvent) => {
    ev.preventDefault();
    setDragging(false);
    void addFiles([...ev.dataTransfer.files]);
  };

  return (
    <div className="mx-auto max-w-6xl">
      <Link to="/kids" className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" aria-hidden="true" /> Kids
      </Link>
      <div className="mb-6 space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">
          Enroll faces{name && <> for <span dir="auto">{name}</span></>}
        </h1>
        <p className="text-sm text-muted-foreground">
          Add clear photos, then click this kid's face in each one. About 10 references from different angles work best.
        </p>
      </div>

      {kid.isError && (
        <Card className="mb-4">
          <CardContent className="flex items-center justify-between gap-3 py-4">
            <p className="text-sm">Could not load the kid: {kid.error.message}</p>
            <Button variant="outline" size="sm" onClick={() => kid.refetch()}>
              <RefreshCw /> Retry
            </Button>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-6 lg:grid-cols-[1fr_20rem]">
        <div className="space-y-4">
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            data-testid="dropzone"
            className={cn(
              "flex flex-col items-center gap-3 rounded-xl border-2 border-dashed px-4 py-8 text-center transition-colors",
              dragging ? "border-primary bg-accent" : "border-border",
            )}
          >
            <Upload className="h-6 w-6 text-muted-foreground" aria-hidden="true" />
            <p className="text-sm">Drop photos here (JPG, PNG, WebP, HEIC; up to 20 MB each)</p>
            <div className="flex flex-wrap justify-center gap-2">
              <Button onClick={() => fileInput.current?.click()} disabled={kid.isPending}>
                Choose files
              </Button>
              <Button variant="outline" onClick={() => setPickerOpen(true)} disabled={kid.isPending}>
                <Images /> Pick from received photos
              </Button>
            </div>
            <input
              ref={fileInput}
              type="file"
              multiple
              accept={ACCEPT}
              className="sr-only"
              aria-label="Choose photos"
              data-testid="file-input"
              onChange={(e) => {
                void addFiles([...(e.target.files ?? [])]);
                e.target.value = "";
              }}
            />
          </div>

          {entries.length > 0 && (
            <div className="grid gap-4 sm:grid-cols-2">
              {entries.map((e) => (
                <PhotoCard
                  key={e.key}
                  entry={e}
                  selected={selected}
                  date={e.result ? (dates[e.result.upload_id] ?? todayISO()) : ""}
                  onToggle={(f) => setSelected((s) => toggleFace(s, f))}
                  onUse={(f) => setSelected((s) => toggleFace(s, f))}
                  onDate={(d) => e.result && setDates((ds) => ({ ...ds, [e.result!.upload_id]: d }))}
                  onRemove={() => remove(e.key)}
                />
              ))}
            </div>
          )}

          {entries.length > 0 && (
            <div className="sticky bottom-3 flex items-center justify-between gap-3 rounded-xl border bg-card px-4 py-3 shadow-sm">
              <p className="text-sm" aria-live="polite">
                {selected.size} {selected.size === 1 ? "face" : "faces"} selected
                {uploading && <span className="text-muted-foreground"> · finding faces…</span>}
              </p>
              <Button disabled={selected.size === 0 || save.isPending} onClick={() => save.mutate()}>
                {save.isPending ? <Loader2 className="animate-spin" /> : <Save />} Save as references
              </Button>
            </div>
          )}
        </div>

        <aside className="space-y-3">
          <Card>
            <CardContent className="pt-6">
              {kid.data ? <CoverageMeter kid={kid.data} /> : <Skeleton className="h-16 w-full" />}
            </CardContent>
          </Card>
        </aside>
      </div>

      <ReceivedPhotosDialog open={pickerOpen} onOpenChange={setPickerOpen} onPick={(ids) => void addReceived(ids)} />
    </div>
  );
}
