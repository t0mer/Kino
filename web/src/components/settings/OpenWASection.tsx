import { useQuery } from "@tanstack/react-query";
import { PlugZap, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { ENV_NAMES, type OpenWAField, openwaSetupApi, openwaSetupKeys } from "@/lib/openwaSetup";

function Row({ label, field, locked, children }: { label: string; field?: OpenWAField; locked: boolean; children: ReactNode }) {
  return (
    <div className="grid gap-1 sm:grid-cols-[10rem_1fr]">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-all text-sm">
        {children}
        {locked && field && (
          <span className="ms-2 text-xs text-muted-foreground">set by {ENV_NAMES[field]}, read-only here</span>
        )}
      </dd>
    </div>
  );
}

/** Current OpenWA connection (secrets shown only as Saved / Not set) and a link to the wizard. */
export function OpenWASection() {
  const status = useQuery({ queryKey: openwaSetupKeys.status, queryFn: openwaSetupApi.status, retry: false });

  let body;
  if (status.isPending) body = <Skeleton className="h-28" aria-label="Loading OpenWA connection" />;
  else if (status.isError)
    body = (
      <div className="flex flex-wrap items-center gap-2">
        <span role="alert" className="text-sm text-destructive">
          Could not load the OpenWA connection: {status.error.message}
        </span>
        <Button variant="outline" size="sm" onClick={() => status.refetch()}>
          <RefreshCw /> Retry
        </Button>
      </div>
    );
  else {
    const s = status.data;
    const locked = new Set<OpenWAField>(s.env_overrides);
    const session = s.session_id ? (s.session_name ? `${s.session_name} (${s.session_id})` : s.session_id) : null;
    body = (
      <dl className="space-y-2">
        <Row label="URL" field="url" locked={locked.has("url")}>
          {s.url ?? "Not set"}
        </Row>
        <Row label="Session" field="session_id" locked={locked.has("session_id")}>
          <span dir="auto">{session ?? "Not set"}</span>
        </Row>
        <Row label="API key" field="api_key" locked={locked.has("api_key")}>
          {s.has_api_key ? "Saved" : "Not set"}
        </Row>
        <Row label="Webhook secret" field="webhook_secret" locked={locked.has("webhook_secret")}>
          {s.has_webhook_secret ? "Saved" : "Not set"}
        </Row>
        <Row label="Webhook URL" locked={false}>
          {s.webhook_url}
        </Row>
      </dl>
    );
  }

  return (
    <Card role="region" aria-labelledby="openwa-title" data-testid="section-openwa">
      <CardContent className="space-y-4 p-5">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h2 id="openwa-title" className="text-lg font-semibold">
              OpenWA
            </h2>
            <p className="text-sm text-muted-foreground">The WhatsApp gateway Kino listens to and sends through.</p>
          </div>
          {status.data && (
            <Badge variant={status.data.configured ? "secondary" : "outline"}>
              {status.data.configured ? "Configured" : "Not configured"}
            </Badge>
          )}
        </div>
        {body}
        <div className="border-t pt-4">
          <Button asChild>
            <Link to="/setup/openwa">
              <PlugZap aria-hidden="true" /> Reconnect
            </Link>
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
