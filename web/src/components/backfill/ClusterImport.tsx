import { Loader2, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { DEFAULT_DAYS } from "@/lib/backfill";
import { groupKeys, groupLabel, groupsApi, sortGroups } from "@/lib/groups";
import { useLive } from "@/lib/query";
import { useBackfill } from "@/lib/useBackfill";
import { JobProgress } from "./JobProgress";

/** Empty-state import of the last 30 days for one group, with a follow-up "Find clusters now". */
export function ClusterImport({ onFindClusters, finding }: { onFindClusters: () => void; finding: boolean }) {
  const groups = useLive(groupKeys.list, groupsApi.list);
  const { jobByGroup, start, starting } = useBackfill();
  const [picked, setPicked] = useState<string>("");
  const [finished, setFinished] = useState<Record<number, string>>({});

  const all = groups.data ?? [];
  const watched = all.filter((g) => g.watched);
  const options = sortGroups(watched.length > 0 ? watched : all);
  const noneWatched = all.length > 0 && watched.length === 0;

  useEffect(() => {
    if (!picked && options.length > 0) setPicked(String(options[0]!.id));
  }, [picked, options]);

  if (groups.isPending) return <p className="text-sm text-muted-foreground">Loading groups</p>;
  if (groups.isError) {
    return (
      <p className="text-sm text-muted-foreground">
        Could not load groups ({groups.error.message}).{" "}
        <button type="button" className="font-medium underline" onClick={() => groups.refetch()}>Retry</button>
      </p>
    );
  }
  if (options.length === 0) {
    return <p className="max-w-sm text-sm text-muted-foreground">No groups seen yet. Open Groups and use Refresh first.</p>;
  }

  const gid = Number(picked);
  const jobId = jobByGroup[gid];
  return (
    <div className="flex w-full max-w-sm flex-col items-stretch gap-3">
      {noneWatched && (
        <p className="text-sm text-muted-foreground">No group is watched yet, so all groups are listed.</p>
      )}
      <div className="grid gap-1.5 text-start">
        <label htmlFor="import-group" className="text-sm font-medium">Group to import from</label>
        <select
          id="import-group"
          value={picked}
          onChange={(e) => setPicked(e.target.value)}
          className="h-9 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {options.map((g) => (
            <option key={g.id} value={g.id} dir="auto">{groupLabel(g)}</option>
          ))}
        </select>
      </div>
      <Button onClick={() => void start(gid, DEFAULT_DAYS)} disabled={starting || jobId !== undefined && finished[jobId] === undefined}>
        {starting && <Loader2 className="animate-spin" />} Import last {DEFAULT_DAYS} days
      </Button>
      {jobId !== undefined && (
        <div className="text-start">
          <JobProgress jobId={jobId} onFinished={(j) => setFinished((f) => ({ ...f, [j.id]: j.status }))} />
        </div>
      )}
      {jobId !== undefined && finished[jobId] === "done" && (
        <Button variant="outline" onClick={onFindClusters} disabled={finding}>
          {finding ? <Loader2 className="animate-spin" /> : <Sparkles />} Find clusters now
        </Button>
      )}
    </div>
  );
}
