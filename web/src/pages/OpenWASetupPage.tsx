import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, ArrowLeft, CheckCircle2, Eye, EyeOff, Loader2, PlugZap, RefreshCw, Sparkles } from "lucide-react";
import { type FormEvent, type ReactNode, useState } from "react";
import { Link, useNavigate } from "react-router";
import { FullPageSpinner } from "@/components/FullPageSpinner";
import { AuthShell } from "@/components/layout/AuthShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "@/components/ui/sonner";
import { ApiError } from "@/lib/api";
import {
  ENV_NAMES,
  type ConnectForm,
  type OpenWAField,
  type OpenWASession,
  type SessionResult,
  connectBody,
  connectErrors,
  generateWebhookSecret,
  openwaSetupApi,
  openwaSetupKeys,
  sessionReady,
} from "@/lib/openwaSetup";
import { settingsKeys } from "@/lib/settings";
import { usePageTitle } from "@/lib/usePageTitle";
import { cn } from "@/lib/utils";

type Step = "connect" | "session" | "done";
const STEPS: { id: Step; label: string }[] = [
  { id: "connect", label: "Connect" },
  { id: "session", label: "Session" },
  { id: "done", label: "Done" },
];

export function errorText(e: unknown): string {
  if (!(e instanceof ApiError)) return "Something went wrong. Try again.";
  if (e.status === 0) return "Cannot reach the Kino server. Check that it is running.";
  return e.detail || `Request failed (HTTP ${e.status}).`;
}

function Alert({ children }: { children: ReactNode }) {
  return (
    <div
      role="alert"
      className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive"
    >
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <span>{children}</span>
    </div>
  );
}

function LockedField({ id, label, field }: { id: string; label: string; field: OpenWAField }) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} value={`Set by ${ENV_NAMES[field]}`} readOnly disabled aria-describedby={`${id}-locked`} />
      <p id={`${id}-locked`} className="text-xs text-muted-foreground">
        Set in the environment or config.yaml. Change it there and restart Kino.
      </p>
    </div>
  );
}

function FieldError({ id, text }: { id: string; text?: string }) {
  return text ? (
    <p id={id} className="text-xs text-destructive">
      {text}
    </p>
  ) : null;
}

export function OpenWASetupPage() {
  usePageTitle("Connect OpenWA");
  const navigate = useNavigate();
  const qc = useQueryClient();
  const status = useQuery({ queryKey: openwaSetupKeys.status, queryFn: openwaSetupApi.status, retry: false });
  const [step, setStep] = useState<Step>("connect");
  const [form, setForm] = useState<ConnectForm>({ url: "", apiKey: "", webhookSecret: "" });
  const [showSecret, setShowSecret] = useState(false);
  const [touched, setTouched] = useState(false);
  const [sessions, setSessions] = useState<OpenWASession[]>([]);
  const [picked, setPicked] = useState("");
  const [webhookUrl, setWebhookUrl] = useState<string | null>(null);
  const [result, setResult] = useState<SessionResult | null>(null);

  const s = status.data;
  const lockedList = s?.env_overrides ?? [];
  const locked = new Set<OpenWAField>(lockedList);
  const sessionLocked = locked.has("session_id");
  const errors = s ? connectErrors(form, s) : {};
  const shown = touched ? errors : {};
  const refreshStatus = () => {
    void qc.invalidateQueries({ queryKey: openwaSetupKeys.status });
    void qc.invalidateQueries({ queryKey: settingsKeys.webhook });
  };

  const connect = useMutation({
    mutationFn: () => openwaSetupApi.connect(connectBody(form, lockedList)),
    onSuccess: (r) => {
      setSessions(r.sessions);
      setPicked((cur) =>
        r.sessions.some((x) => x.id === cur) ? cur : (r.sessions.find(sessionReady) ?? r.sessions[0])?.id ?? "",
      );
      if (r.generated_webhook_secret) toast.success("Kino generated a webhook secret for you.");
      setStep("session");
      refreshStatus();
    },
  });

  const chosenUrl = webhookUrl === null ? undefined : webhookUrl.trim() || undefined;
  const pick = useMutation({
    mutationFn: async (): Promise<SessionResult> => {
      if (!sessionLocked) return openwaSetupApi.pickSession(picked, chosenUrl);
      const target = chosenUrl ?? s?.webhook_url ?? "";
      const sid = s?.session_id ?? "";
      try {
        const r = await openwaSetupApi.registerWebhook(target);
        return { session_id: sid, registered: true, action: r.action, webhook_id: r.id, webhook_url: r.url, error: null };
      } catch (e) {
        return { session_id: sid, registered: false, action: null, webhook_id: null, webhook_url: target, error: errorText(e) };
      }
    },
    onSuccess: (r) => {
      setResult(r);
      setStep("done");
      refreshStatus();
    },
    onError: (e) => {
      if (e instanceof ApiError && e.status === 404) connect.mutate(); // the session vanished: reload
    },
  });

  const retry = useMutation({
    mutationFn: () => openwaSetupApi.registerWebhook(result?.webhook_url ?? ""),
    onSuccess: (r) => {
      setResult((cur) => cur && { ...cur, registered: true, action: r.action, webhook_id: r.id, error: null });
      refreshStatus();
    },
  });

  if (status.isPending) return <FullPageSpinner />;

  const onConnect = (ev: FormEvent) => {
    ev.preventDefault();
    setTouched(true);
    if (Object.keys(errors).length === 0) connect.mutate();
  };
  const finish = () => {
    refreshStatus();
    navigate("/review", { replace: true });
  };

  return (
    <AuthShell wide>
      <Card className="shadow-xl shadow-black/5">
        <CardHeader className="space-y-3">
          <div className="flex items-center justify-between gap-2">
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-accent text-accent-foreground">
              <PlugZap className="h-5 w-5" aria-hidden="true" />
            </div>
            {s?.configured && (
              <Button variant="ghost" size="sm" asChild>
                <Link to="/settings">
                  <ArrowLeft aria-hidden="true" /> Back to Settings
                </Link>
              </Button>
            )}
          </div>
          <h1 className="text-xl font-semibold leading-none tracking-tight">Connect Kino to OpenWA</h1>
          <CardDescription>
            Kino receives group photos from OpenWA and sends forwards through it. This takes about a minute.
          </CardDescription>
          <ol aria-label="Setup steps" className="flex gap-2 text-xs">
            {STEPS.map((x, i) => (
              <li
                key={x.id}
                aria-current={x.id === step ? "step" : undefined}
                className={cn(
                  "rounded-full border px-2.5 py-1",
                  x.id === step ? "border-primary text-foreground" : "text-muted-foreground",
                )}
              >
                {i + 1}. {x.label}
              </li>
            ))}
          </ol>
        </CardHeader>
        <CardContent>
          {status.isError && (
            <div className="space-y-3">
              <Alert>{errorText(status.error)}</Alert>
              <Button variant="outline" onClick={() => void status.refetch()} disabled={status.isFetching}>
                <RefreshCw aria-hidden="true" /> Try again
              </Button>
            </div>
          )}

          {step === "connect" && s && (
            <form className="space-y-4" onSubmit={onConnect} noValidate>
              {locked.has("url") ? (
                <LockedField id="owa-url" label="OpenWA URL" field="url" />
              ) : (
                <div className="space-y-1.5">
                  <Label htmlFor="owa-url">OpenWA URL</Label>
                  <Input
                    id="owa-url"
                    type="url"
                    inputMode="url"
                    autoComplete="off"
                    autoFocus
                    placeholder={s.url ?? "http://openwa:2785"}
                    value={form.url}
                    onChange={(e) => setForm({ ...form, url: e.target.value })}
                    aria-invalid={shown.url ? true : undefined}
                    aria-describedby="owa-url-hint owa-url-error"
                  />
                  <p id="owa-url-hint" className="text-xs text-muted-foreground">
                    {s.url ? `Leave empty to keep ${s.url}.` : "The address Kino uses to reach OpenWA."}
                  </p>
                  <FieldError id="owa-url-error" text={shown.url} />
                </div>
              )}

              {locked.has("api_key") ? (
                <LockedField id="owa-key" label="API key" field="api_key" />
              ) : (
                <div className="space-y-1.5">
                  <Label htmlFor="owa-key">API key</Label>
                  <Input
                    id="owa-key"
                    type="password"
                    autoComplete="off"
                    placeholder={s.has_api_key ? "Leave empty to keep the saved key" : ""}
                    value={form.apiKey}
                    onChange={(e) => setForm({ ...form, apiKey: e.target.value })}
                    aria-invalid={shown.apiKey ? true : undefined}
                    aria-describedby="owa-key-hint owa-key-error"
                  />
                  {s.has_api_key && (
                    <p id="owa-key-hint" className="text-xs text-muted-foreground">
                      Leave empty to keep the saved key. Enter it again when you change the URL.
                    </p>
                  )}
                  <FieldError id="owa-key-error" text={shown.apiKey} />
                </div>
              )}

              {locked.has("webhook_secret") ? (
                <LockedField id="owa-secret" label="Webhook secret" field="webhook_secret" />
              ) : (
                <div className="space-y-1.5">
                  <Label htmlFor="owa-secret">Webhook secret</Label>
                  <div className="flex gap-2">
                    <Input
                      id="owa-secret"
                      type={showSecret ? "text" : "password"}
                      autoComplete="off"
                      spellCheck={false}
                      className="font-mono"
                      value={form.webhookSecret}
                      onChange={(e) => setForm({ ...form, webhookSecret: e.target.value })}
                      aria-invalid={(form.webhookSecret.trim() !== "" && !!errors.webhookSecret) || undefined}
                      aria-describedby="owa-secret-hint owa-secret-error"
                    />
                    <Button
                      type="button"
                      variant="outline"
                      size="icon"
                      aria-label={showSecret ? "Hide webhook secret" : "Show webhook secret"}
                      aria-pressed={showSecret}
                      onClick={() => setShowSecret((v) => !v)}
                    >
                      {showSecret ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => {
                        setForm({ ...form, webhookSecret: generateWebhookSecret() });
                        setShowSecret(true);
                      }}
                    >
                      <Sparkles aria-hidden="true" /> Generate
                    </Button>
                  </div>
                  <p id="owa-secret-hint" className="text-xs text-muted-foreground">
                    OpenWA signs every webhook with it. At least 16 characters. Leave empty to{" "}
                    {s.has_webhook_secret ? "keep the saved one" : "let Kino generate one"}.
                  </p>
                  <FieldError
                    id="owa-secret-error"
                    text={form.webhookSecret.trim() !== "" || touched ? errors.webhookSecret : undefined}
                  />
                </div>
              )}

              {connect.isError && <Alert>{errorText(connect.error)}</Alert>}
              <Button type="submit" className="w-full" disabled={connect.isPending}>
                {connect.isPending && <Loader2 className="animate-spin" aria-hidden="true" />}
                Test and continue
              </Button>
            </form>
          )}

          {step === "session" && s && (
            <div className="space-y-4">
              {sessionLocked ? (
                <p className="text-sm">
                  The session is set by {ENV_NAMES.session_id}:{" "}
                  <span dir="auto" className="font-medium">
                    {s.session_id}
                  </span>
                  . Kino will register its webhook for it.
                </p>
              ) : sessions.length === 0 ? (
                <div className="space-y-3 text-sm">
                  <p>
                    OpenWA has no sessions yet. Create one in OpenWA and scan its QR code with WhatsApp, then
                    reload.
                  </p>
                  <Button variant="outline" onClick={() => connect.mutate()} disabled={connect.isPending}>
                    <RefreshCw aria-hidden="true" /> Reload sessions
                  </Button>
                </div>
              ) : (
                <fieldset className="space-y-2">
                  <legend className="mb-2 text-sm font-medium">WhatsApp session</legend>
                  {sessions.map((x) => {
                    const ready = sessionReady(x);
                    return (
                      <label
                        key={x.id}
                        className="flex cursor-pointer items-start gap-3 rounded-md border p-3 has-[:checked]:border-primary has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring"
                      >
                        <input
                          type="radio"
                          name="owa-session"
                          value={x.id}
                          checked={picked === x.id}
                          onChange={() => setPicked(x.id)}
                          className="mt-1 accent-primary"
                        />
                        <span className="min-w-0 flex-1 space-y-1">
                          <span className="flex flex-wrap items-center gap-2">
                            <span dir="auto" className="font-medium">
                              {x.name ?? x.id}
                            </span>
                            <Badge variant={ready ? "secondary" : "outline"}>{x.status}</Badge>
                          </span>
                          <span className="block break-all text-xs text-muted-foreground">
                            {x.phone_masked ?? "No phone linked"} · id {x.id}
                          </span>
                          {!ready && (
                            <span className="block text-xs text-muted-foreground">
                              Not connected yet: Kino receives messages only once this session is ready in OpenWA.
                            </span>
                          )}
                        </span>
                      </label>
                    );
                  })}
                </fieldset>
              )}

              {s.webhook_secret_pending && (
                <p className="text-sm text-muted-foreground">
                  New webhook secret not applied yet: finish the wizard so OpenWA signs with it. Until then
                  Kino keeps checking webhooks with the old secret.
                </p>
              )}

              <div className="space-y-1.5">
                <Label htmlFor="owa-hook">Webhook URL</Label>
                <Input
                  id="owa-hook"
                  type="url"
                  value={webhookUrl ?? s.webhook_url}
                  onChange={(e) => setWebhookUrl(e.target.value)}
                  aria-describedby="owa-hook-hint"
                />
                <p id="owa-hook-hint" className="text-xs text-muted-foreground">
                  The address OpenWA calls for new messages. It must be reachable from OpenWA.
                </p>
              </div>

              {pick.isError && <Alert>{errorText(pick.error)}</Alert>}
              <div className="flex flex-wrap gap-2">
                <Button variant="ghost" onClick={() => setStep("connect")}>
                  Back
                </Button>
                <Button
                  className="flex-1"
                  onClick={() => pick.mutate()}
                  disabled={pick.isPending || (!sessionLocked && !picked)}
                >
                  {pick.isPending && <Loader2 className="animate-spin" aria-hidden="true" />}
                  {sessionLocked ? "Register webhook" : "Use this session"}
                </Button>
              </div>
            </div>
          )}

          {step === "done" && result && (
            <div className="space-y-4">
              {result.registered ? (
                <div className="flex items-start gap-2 text-sm">
                  <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-success" aria-hidden="true" />
                  <div>
                    <p className="font-medium">Webhook {result.action}</p>
                    <p className="break-all text-muted-foreground">
                      OpenWA now sends new group photos to {result.webhook_url}.
                    </p>
                  </div>
                </div>
              ) : (
                <>
                  <Alert>{result.error ?? "The webhook was not registered."}</Alert>
                  <Button variant="outline" onClick={() => retry.mutate()} disabled={retry.isPending}>
                    {retry.isPending ? <Loader2 className="animate-spin" aria-hidden="true" /> : <RefreshCw aria-hidden="true" />}
                    Retry registration
                  </Button>
                  {retry.isError && <Alert>{errorText(retry.error)}</Alert>}
                </>
              )}
              <Button className="w-full" onClick={finish}>
                Open Kino
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
    </AuthShell>
  );
}
