import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PlugZap, RefreshCw } from "lucide-react";
import { type ReactNode, useState } from "react";
import { Link } from "react-router";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/sonner";
import {
  ENV_NAMES,
  type OpenWAField,
  type OpenWASetupStatus,
  openwaSetupApi,
  openwaSetupKeys,
  sessionReady,
} from "@/lib/openwaSetup";

const SAME = "__same__";

/** Choose which OpenWA session sends photos and the digest. The session list is fetched only on request. */
function SendingSession({ s }: { s: OpenWASetupStatus }) {
  const qc = useQueryClient();
  const [wanted, setWanted] = useState(false);
  const sessions = useQuery({
    queryKey: openwaSetupKeys.sessions,
    queryFn: openwaSetupApi.sessions,
    enabled: wanted,
    retry: false,
    staleTime: 0,
  });
  const [error, setError] = useState<string | null>(null);
  const incoming = s.session_name ?? s.session_id ?? "not set";
  const current = s.same_as_incoming || !s.outgoing_session_id ? SAME : s.outgoing_session_id;

  const save = useMutation({
    mutationFn: (id: string | null) => openwaSetupApi.setOutgoingSession(id),
    onSuccess: () => {
      setError(null);
      toast.success("Sending session saved");
      qc.invalidateQueries({ queryKey: openwaSetupKeys.status });
      qc.invalidateQueries({ queryKey: ["openwa", "chats"] });
    },
    onError: (e: Error) => {
      setError(e.message);
      toast.error("Could not save the sending session", { description: e.message });
    },
  });

  const list = sessions.data ?? [];
  const picked = list.find((x) => x.id === current);
  const known = current === SAME || picked;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor="outgoing-session" className="text-sm text-muted-foreground sm:w-40">
          Sending session
        </label>
        <select
          id="outgoing-session"
          className="h-9 min-w-0 max-w-full rounded-md border bg-background px-2 text-sm"
          value={current}
          disabled={save.isPending}
          onFocus={() => setWanted(true)}
          onChange={(e) => save.mutate(e.target.value === SAME ? null : e.target.value)}
        >
          <option value={SAME}>Same as incoming ({incoming})</option>
          {!known && s.outgoing_session_id && (
            <option value={s.outgoing_session_id}>{s.outgoing_session_name ?? s.outgoing_session_id}</option>
          )}
          {list
            .filter((x) => x.id !== s.session_id)
            .map((x) => (
              <option key={x.id} value={x.id}>
                {x.name ?? x.id}
                {x.phone_masked ? ` · ${x.phone_masked}` : ""} · {x.status}
              </option>
            ))}
        </select>
        {!wanted && (
          <Button variant="outline" size="sm" onClick={() => setWanted(true)}>
            Load sessions
          </Button>
        )}
        {sessions.isFetching && <span className="text-xs text-muted-foreground">Loading sessions…</span>}
      </div>
      {sessions.isError && (
        <div className="flex flex-wrap items-center gap-2">
          <span role="alert" className="text-sm text-destructive">
            Could not load sessions: {sessions.error.message}
          </span>
          <Button variant="outline" size="sm" onClick={() => sessions.refetch()}>
            <RefreshCw /> Retry
          </Button>
        </div>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {picked && !sessionReady(picked) && current !== SAME && (
        <p className="text-sm text-amber-600 dark:text-amber-400">
          This session is not connected ({picked.status}). Sends will fail until it is ready.
        </p>
      )}
      {sessions.isSuccess && current !== SAME && !picked && (
        <p className="text-sm text-amber-600 dark:text-amber-400">
          This session no longer exists in OpenWA. Pick another or switch back to the incoming session.
        </p>
      )}
      <p className="text-xs text-muted-foreground">
        Photos and the digest are sent from this session. Incoming photos are always received on {incoming}.
      </p>
    </div>
  );
}

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
        {status.data?.configured && status.data.session_id && <SendingSession s={status.data} />}
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
