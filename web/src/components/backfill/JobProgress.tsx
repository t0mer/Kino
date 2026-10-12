import { AlertCircle, CheckCircle2, Loader2 } from "lucide-react";
import { useEffect, useRef } from "react";
import { type Job, isActive, jobFraction, jobSummary } from "@/lib/backfill";
import { useJob } from "@/lib/useBackfill";

/** Inline progress of a backfill job: polls until done/failed. */
export function JobProgress({ jobId, onFinished }: { jobId: number; onFinished?: (job: Job) => void }) {
  const q = useJob(jobId);
  const job = q.data;
  const fired = useRef(false);
  useEffect(() => {
    if (job && !isActive(job.status) && !fired.current) {
      fired.current = true;
      onFinished?.(job);
    }
  }, [job, onFinished]);

  if (!job) {
    return q.isError ? (
      <p role="alert" className="text-sm text-destructive">Could not read the backfill progress: {q.error.message}</p>
    ) : (
      <p className="flex items-center gap-2 text-sm text-muted-foreground" aria-live="polite">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> Loading progress
      </p>
    );
  }
  if (job.status === "failed") {
    return (
      <p role="alert" className="flex items-start gap-2 rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
        <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        <span>Backfill failed{job.error ? `: ${job.error}` : ""}{job.processed > 0 ? ` (${jobSummary(job)})` : ""}</span>
      </p>
    );
  }
  if (job.status === "done") {
    return (
      <p className="flex items-center gap-2 text-sm" role="status">
        <CheckCircle2 className="h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
        Backfill finished: {jobSummary(job)}
      </p>
    );
  }
  const f = jobFraction(job);
  return (
    <div className="space-y-1.5" role="status" aria-live="polite" data-testid={`job-${job.id}`}>
      <p className="flex items-center gap-2 text-sm">
        <Loader2 className="h-4 w-4 shrink-0 animate-spin text-muted-foreground" aria-hidden="true" />
        {jobSummary(job)}
      </p>
      <div
        role="progressbar"
        aria-label="Backfill progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={f === null ? undefined : Math.round(f * 100)}
        className="h-1.5 overflow-hidden rounded-full bg-muted"
      >
        <div
          className={f === null ? "h-full w-1/3 animate-pulse rounded-full bg-primary" : "h-full rounded-full bg-primary transition-all"}
          style={f === null ? undefined : { width: `${Math.round(f * 100)}%` }}
        />
      </div>
    </div>
  );
}
