import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, Webhook } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/sonner";
import { type SettingsOut, settingsApi, settingsKeys } from "@/lib/settings";
import { ConfirmDialog } from "./SectionCard";

function WebhookPanel() {
  const qc = useQueryClient();
  const [confirm, setConfirm] = useState(false);
  const status = useQuery({ queryKey: settingsKeys.webhook, queryFn: settingsApi.webhook, retry: false });
  const register = useMutation({
    mutationFn: settingsApi.registerWebhook,
    onSuccess: (r) => {
      toast.success(`Webhook ${r.action}`);
      void qc.invalidateQueries({ queryKey: settingsKeys.webhook });
    },
    onError: (e) => toast.error("Could not register the webhook", { description: e.message }),
    onSettled: () => setConfirm(false),
  });

  let state;
  if (status.isPending) state = <Skeleton className="h-6 w-48" aria-label="Loading webhook status" />;
  else if (status.isError)
    state = (
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-destructive" role="alert">
          Could not check the webhook: {status.error.message}
        </span>
        <Button variant="outline" size="sm" onClick={() => status.refetch()}>
          <RefreshCw /> Retry
        </Button>
      </div>
    );
  else {
    const s = status.data;
    state = (
      <div className="space-y-1 text-sm">
        <div className="flex items-center gap-2">
          {s.registered ? (
            <Badge variant={s.active === false ? "outline" : "secondary"}>{s.active === false ? "Registered, inactive" : "Registered"}</Badge>
          ) : (
            <Badge variant="outline">Not registered</Badge>
          )}
        </div>
        {s.url && <p className="break-all text-muted-foreground">{s.url}</p>}
        {s.events.length > 0 && <p className="text-xs text-muted-foreground">Events: {s.events.join(", ")}</p>}
      </div>
    );
  }
  const registered = status.data?.registered;

  return (
    <div className="space-y-2">
      <p className="text-sm font-medium">OpenWA webhook</p>
      {state}
      <Button variant="outline" size="sm" onClick={() => setConfirm(true)} disabled={register.isPending}>
        <Webhook /> {registered ? "Repair webhook" : "Register webhook"}
      </Button>
      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title={registered ? "Repair the webhook?" : "Register the webhook?"}
        description={<p>This creates or updates Kino&apos;s webhook in OpenWA so new group photos reach Kino.</p>}
        confirmLabel={registered ? "Repair webhook" : "Register webhook"}
        pending={register.isPending}
        onConfirm={() => register.mutate()}
      />
    </div>
  );
}

export function SystemSection({ data }: { data: SettingsOut }) {
  return (
    <Card role="region" aria-labelledby="system-title" data-testid="section-system">
      <CardContent className="space-y-4 p-5">
        <div>
          <h2 id="system-title" className="text-lg font-semibold">
            Models and system
          </h2>
          <p className="text-sm text-muted-foreground">Read-only. Kino {data.version}</p>
        </div>
        <dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-[auto_1fr]">
          <dt className="text-muted-foreground">Version</dt>
          <dd>{data.version}</dd>
          <dt className="text-muted-foreground">Embedder</dt>
          <dd>{data.embedder}</dd>
        </dl>
        <ul className="space-y-2">
          {data.models.map((m) => (
            <li key={m.name} className="rounded-lg border p-3 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-medium">{m.name}</span>
                <Badge variant={m.present ? "secondary" : "outline"}>{m.present ? "Downloaded" : "Not downloaded"}</Badge>
              </div>
              <p className="text-muted-foreground">License: {m.license}</p>
              <p className="break-all font-mono text-xs text-muted-foreground" title={m.sha256}>
                SHA-256 {m.sha256}
              </p>
            </li>
          ))}
        </ul>
        <WebhookPanel />
      </CardContent>
    </Card>
  );
}
