import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, MessagesSquare, RefreshCw, Send } from "lucide-react";
import { type ReactNode, useState } from "react";
import { BackfillDialog } from "@/components/backfill/BackfillDialog";
import { JobProgress } from "@/components/backfill/JobProgress";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/sonner";
import { Switch } from "@/components/ui/switch";
import { formatRelative } from "@/lib/format";
import { type GroupItem, groupKeys, groupLabel, groupsApi, refreshSummary, sortGroups, toggleKid } from "@/lib/groups";
import { type Kid, kidKeys, kidsApi } from "@/lib/kids";
import { useLive } from "@/lib/query";
import { cn } from "@/lib/utils";
import { useBackfill } from "@/lib/useBackfill";
import { usePageTitle } from "@/lib/usePageTitle";
import { PageHeader } from "./PlaceholderPage";

function GroupRow({
  group,
  kids,
  busy,
  jobId,
  onWatch,
  onKids,
  onBackfill,
}: {
  group: GroupItem;
  kids: Kid[];
  busy: boolean;
  jobId: number | undefined;
  onWatch: (g: GroupItem, watched: boolean) => void;
  onKids: (g: GroupItem, ids: number[]) => void;
  onBackfill: (g: GroupItem) => void;
}) {
  const name = groupLabel(group);
  return (
    <Card data-testid={`group-${group.id}`}>
      <CardContent className="space-y-3 p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="truncate font-medium" dir="auto">
              {name}
            </h2>
            <p className="text-xs text-muted-foreground">
              {group.last_seen_at ? `Last seen ${formatRelative(group.last_seen_at)}` : "No messages seen yet"}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => onBackfill(group)} aria-label={`Backfill ${name}`}>
              Backfill
            </Button>
            {busy && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-hidden="true" />}
            <label className="flex items-center gap-2 text-sm">
              <span>{group.watched ? "Watched" : "Not watched"}</span>
              <Switch
                aria-label={`Watch ${name}`}
                checked={group.watched}
                disabled={busy}
                onCheckedChange={(v) => onWatch(group, v)}
              />
            </label>
          </div>
        </div>

        {group.is_forward_destination && (
          <p className="flex items-center gap-2 rounded-md bg-accent px-3 py-2 text-sm text-accent-foreground">
            <Send className="h-4 w-4 shrink-0" aria-hidden="true" />
            This group is the forward destination, so it cannot be watched (forwarded photos would be ingested again).
          </p>
        )}

        {jobId !== undefined && <JobProgress jobId={jobId} />}

        <div>
          <p className="mb-1.5 text-xs font-medium text-muted-foreground" id={`kids-label-${group.id}`}>
            Kids likely to appear here
          </p>
          {kids.length === 0 ? (
            <p className="text-sm text-muted-foreground">Add a kid first to map it to this group.</p>
          ) : (
            <div role="group" aria-labelledby={`kids-label-${group.id}`} className="flex flex-wrap gap-2">
              {kids.map((k) => {
                const on = group.kid_ids.includes(k.id);
                return (
                  <button
                    key={k.id}
                    type="button"
                    aria-pressed={on}
                    disabled={busy}
                    onClick={() => onKids(group, toggleKid(group.kid_ids, k.id))}
                    className={cn(
                      "rounded-full border px-3 py-1 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50",
                      on
                        ? "border-transparent bg-primary text-primary-foreground"
                        : "border-input bg-background hover:bg-accent",
                    )}
                  >
                    <span dir="auto">{k.display_name}</span>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

export function GroupsPage() {
  usePageTitle("Groups");
  const qc = useQueryClient();
  const groups = useLive(groupKeys.list, groupsApi.list);
  const kids = useLive(kidKeys.list, kidsApi.list);
  const { jobByGroup, start, starting } = useBackfill();
  const [backfillFor, setBackfillFor] = useState<GroupItem | null>(null);

  const update = useMutation({
    mutationFn: ({ group, body }: { group: GroupItem; body: Parameters<typeof groupsApi.update>[1] }) =>
      groupsApi.update(group.id, body),
    onSuccess: (saved, { body }) => {
      qc.setQueryData<GroupItem[]>(groupKeys.list, (list) => list?.map((g) => (g.id === saved.id ? saved : g)));
      if (body.watched !== undefined) {
        toast.success(
          body.watched ? `Now watching ${groupLabel(saved)}` : `Stopped watching ${groupLabel(saved)}`,
        );
      } else {
        toast.success(`Updated kids for ${groupLabel(saved)}`);
      }
      void qc.invalidateQueries({ queryKey: kidKeys.list });
    },
    onError: (e, { group }) => {
      toast.error(`Could not update ${groupLabel(group)}`, { description: e.message });
      void qc.invalidateQueries({ queryKey: groupKeys.list });
    },
  });

  const refresh = useMutation({
    mutationFn: groupsApi.refresh,
    onSuccess: (r) => {
      toast.success(refreshSummary(r));
      void qc.invalidateQueries({ queryKey: groupKeys.list });
    },
    onError: (e) => toast.error("Could not refresh groups", { description: e.message }),
  });

  const refreshButton = (
    <Button variant="outline" onClick={() => refresh.mutate()} disabled={refresh.isPending}>
      {refresh.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />} Refresh
    </Button>
  );

  let body: ReactNode;
  if (groups.isPending) {
    body = (
      <div className="space-y-3" aria-busy="true" aria-label="Loading groups">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-32 rounded-xl" />
        ))}
      </div>
    );
  } else if (groups.isError) {
    body = (
      <Card>
        <CardContent className="flex flex-col items-center gap-3 py-12 text-center">
          <p className="font-medium">Could not load groups</p>
          <p className="text-sm text-muted-foreground">{groups.error.message}</p>
          <Button variant="outline" onClick={() => groups.refetch()}>
            <RefreshCw /> Retry
          </Button>
        </CardContent>
      </Card>
    );
  } else if (groups.data.length === 0) {
    body = (
      <Card className="border-dashed">
        <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-accent text-accent-foreground">
            <MessagesSquare className="h-6 w-6" aria-hidden="true" />
          </div>
          <p className="font-medium">No groups seen yet</p>
          <p className="max-w-md text-sm text-muted-foreground">
            No groups seen yet. Post a photo in a WhatsApp group the Kino session belongs to, or use Refresh.
          </p>
          {refreshButton}
        </CardContent>
      </Card>
    );
  } else {
    const watchedCount = groups.data.filter((g) => g.watched).length;
    body = (
      <>
        <p className="mb-3 text-sm text-muted-foreground">
          <Badge variant="secondary">{watchedCount}</Badge> of {groups.data.length} groups watched. New groups start
          unwatched.
        </p>
        <div className="space-y-3">
          {sortGroups(groups.data).map((g) => (
            <GroupRow
              key={g.id}
              group={g}
              kids={kids.data ?? []}
              busy={update.isPending && update.variables?.group.id === g.id}
              jobId={jobByGroup[g.id]}
              onBackfill={setBackfillFor}
              onWatch={(group, watched) => update.mutate({ group, body: { watched } })}
              onKids={(group, ids) => update.mutate({ group, body: { kid_ids: ids } })}
            />
          ))}
        </div>
      </>
    );
  }

  return (
    <div className="mx-auto max-w-4xl">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <PageHeader title="Groups" description="Choose which WhatsApp groups Kino watches and which kids appear in them." />
        <div className="flex gap-2">
          {groups.data && groups.data.length > 0 && refreshButton}
        </div>
      </div>
      {body}
      {backfillFor && (
        <BackfillDialog
          groupName={groupLabel(backfillFor)}
          starting={starting}
          onStart={(days) => start(backfillFor.id, days)}
          onClose={() => setBackfillFor(null)}
        />
      )}
    </div>
  );
}
