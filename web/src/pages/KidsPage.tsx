import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, RefreshCw, Users } from "lucide-react";
import { type ReactNode, useState } from "react";
import { DeleteKidDialog } from "@/components/kids/DeleteKidDialog";
import { KidCard } from "@/components/kids/KidCard";
import { KidFormDialog } from "@/components/kids/KidFormDialog";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/sonner";
import { type Kid, kidKeys, kidsApi } from "@/lib/kids";
import { useLive } from "@/lib/query";
import { usePageTitle } from "@/lib/usePageTitle";
import { PageHeader } from "./PlaceholderPage";

function useAutoSaveToggle() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ kid, enabled }: { kid: Kid; enabled: boolean }) =>
      kidsApi.update(kid.id, { auto_save_enabled: enabled }),
    onMutate: async ({ kid, enabled }) => {
      await qc.cancelQueries({ queryKey: kidKeys.list });
      const prev = qc.getQueryData<Kid[]>(kidKeys.list);
      qc.setQueryData<Kid[]>(kidKeys.list, (list) =>
        list?.map((k) => (k.id === kid.id ? { ...k, auto_save_enabled: enabled } : k)),
      );
      return { prev };
    },
    onError: (e, { kid }, ctx) => {
      if (ctx?.prev) qc.setQueryData(kidKeys.list, ctx.prev);
      toast.error(`Could not change auto-save for ${kid.display_name}`, { description: e.message });
    },
    onSuccess: (saved) => {
      toast.success(`Auto-save ${saved.auto_save_enabled ? "on" : "off"} for ${saved.display_name}`);
    },
    onSettled: () => qc.invalidateQueries({ queryKey: kidKeys.list }),
  });
}

export function KidsPage() {
  usePageTitle("Kids");
  const kids = useLive(kidKeys.list, kidsApi.list);
  const toggle = useAutoSaveToggle();
  const [editing, setEditing] = useState<{ open: boolean; kid: Kid | null }>({ open: false, kid: null });
  const [deleting, setDeleting] = useState<Kid | null>(null);

  const addButton = (
    <Button onClick={() => setEditing({ open: true, kid: null })}>
      <Plus /> Add kid
    </Button>
  );

  let body: ReactNode;
  if (kids.isPending) {
    body = (
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3" aria-busy="true" aria-label="Loading kids">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-96 rounded-xl" />
        ))}
      </div>
    );
  } else if (kids.isError) {
    body = (
      <Card>
        <CardContent className="flex flex-col items-center gap-3 py-12 text-center">
          <p className="font-medium">Could not load kids</p>
          <p className="text-sm text-muted-foreground">{kids.error.message}</p>
          <Button variant="outline" onClick={() => kids.refetch()}>
            <RefreshCw /> Retry
          </Button>
        </CardContent>
      </Card>
    );
  } else if (kids.data.length === 0) {
    body = (
      <Card className="border-dashed">
        <CardContent className="flex flex-col items-center gap-3 py-16 text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-accent text-accent-foreground">
            <Users className="h-6 w-6" aria-hidden="true" />
          </div>
          <p className="font-medium">No kids yet</p>
          <p className="max-w-sm text-sm text-muted-foreground">
            Add each of your kids, then enroll a few clear photos of their face so Kino can find them.
          </p>
          {addButton}
        </CardContent>
      </Card>
    );
  } else {
    body = (
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {kids.data.map((k) => (
          <KidCard
            key={k.id}
            kid={k}
            onEdit={(kid) => setEditing({ open: true, kid })}
            onDelete={setDeleting}
            onToggleAutoSave={(kid, enabled) => toggle.mutate({ kid, enabled })}
            toggling={toggle.isPending && toggle.variables?.kid.id === k.id}
          />
        ))}
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <PageHeader title="Kids" description="Your kids, their reference faces, coverage and auto-save." />
        {kids.data && kids.data.length > 0 && addButton}
      </div>
      {body}
      <KidFormDialog
        open={editing.open}
        kid={editing.kid}
        onOpenChange={(open) => setEditing((s) => ({ ...s, open }))}
      />
      <DeleteKidDialog kid={deleting} onClose={() => setDeleting(null)} />
    </div>
  );
}
