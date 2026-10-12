import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo, useState } from "react";
import { toast } from "@/components/ui/sonner";
import { isApiError } from "./api";
import { activeJobsByGroup, jobKeys, jobRefetchInterval, jobsApi } from "./backfill";
import { groupKeys } from "./groups";

/** One job, polled every ~2 s until it is done or failed. */
export function useJob(jobId: number | null) {
  return useQuery({
    queryKey: jobKeys.one(jobId ?? -1),
    queryFn: () => jobsApi.get(jobId as number),
    enabled: jobId !== null,
    refetchInterval: (q) => jobRefetchInterval(q.state.data),
    refetchIntervalInBackground: false,
    staleTime: 0,
  });
}

/**
 * Backfill control shared by the Groups page and the clusters empty state.
 * `jobByGroup` maps group id to a job id: jobs started or attached here, plus any queued/running
 * backfill the server reports on load.
 */
export function useBackfill() {
  const qc = useQueryClient();
  const [attached, setAttached] = useState<Record<number, number>>({});
  const list = useQuery({ queryKey: jobKeys.backfillList, queryFn: () => jobsApi.list(), staleTime: 0 });
  const seeded = useMemo(() => activeJobsByGroup(list.data), [list.data]);
  const jobByGroup = useMemo(() => ({ ...seeded, ...attached }), [seeded, attached]);
  const [starting, setStarting] = useState(false);

  /** Start a backfill. Resolves true when a job (new or already running) is now tracked. */
  const start = useCallback(
    async (groupId: number, days: number): Promise<boolean> => {
      setStarting(true);
      try {
        const { job_id } = await jobsApi.startBackfill(groupId, days);
        setAttached((a) => ({ ...a, [groupId]: job_id }));
        toast.success("Backfill started", { description: `Fetching photos from the last ${days} days.` });
        return true;
      } catch (e) {
        if (isApiError(e, 409)) {
          try {
            const jobs = await qc.fetchQuery({ queryKey: jobKeys.backfillList, queryFn: () => jobsApi.list(), staleTime: 0 });
            const running = activeJobsByGroup(jobs)[groupId];
            if (running !== undefined) {
              setAttached((a) => ({ ...a, [groupId]: running }));
              toast.info("A backfill is already running for this group", { description: "Showing its progress." });
              return true;
            }
          } catch {
            /* fall through to the generic message */
          }
          toast.info("A backfill is already queued or running for this group");
          return false;
        }
        if (isApiError(e, 404)) {
          toast.error("That group no longer exists");
          void qc.invalidateQueries({ queryKey: groupKeys.list });
        } else {
          toast.error("Could not start the backfill", { description: e instanceof Error ? e.message : undefined });
        }
        return false;
      } finally {
        setStarting(false);
      }
    },
    [qc],
  );

  return { jobByGroup, start, starting };
}
