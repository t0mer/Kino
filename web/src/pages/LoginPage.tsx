import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Loader2, LockKeyhole } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router";
import { FullPageSpinner } from "@/components/FullPageSpinner";
import { AuthShell } from "@/components/layout/AuthShell";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, isApiError } from "@/lib/api";
import { authApi, authKeys, safeNext } from "@/lib/auth";
import { formatDuration } from "@/lib/format";
import { usePageTitle } from "@/lib/usePageTitle";

function loginErrorMessage(e: unknown, lockedFor: number): string {
  if (!(e instanceof ApiError)) return "Something went wrong. Try again.";
  switch (e.status) {
    case 401:
      return "Wrong username or password.";
    case 429:
      return lockedFor > 0
        ? `Too many failed attempts. Try again in ${formatDuration(lockedFor)}.`
        : "Too many failed attempts. You can try again now.";
    case 403:
      return "The security cookie is missing or blocked. Reload the page and try again.";
    case 0:
      return "Cannot reach the Kino server. Check that it is running.";
    default:
      return e.detail || `Sign-in failed (HTTP ${e.status}).`;
  }
}

export function LoginPage() {
  usePageTitle("Sign in");
  const [params] = useSearchParams();
  const next = safeNext(params.get("next"));
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [lockedUntil, setLockedUntil] = useState(0);
  const [now, setNow] = useState(() => Date.now());

  const status = useQuery({ queryKey: authKeys.status, queryFn: authApi.status, retry: false });
  // Already signed in (e.g. through Cloudflare Access): skip the form.
  const me = useQuery({ queryKey: authKeys.me, queryFn: authApi.me, retry: false });

  const login = useMutation({
    mutationFn: () => authApi.login(username.trim(), password),
    onSuccess: (data) => {
      qc.setQueryData(authKeys.me, data);
      navigate(next, { replace: true });
    },
    onError: (e) => {
      setPassword("");
      if (isApiError(e, 429)) setLockedUntil(Date.now() + (e.retryAfter ?? 60) * 1000);
      if (isApiError(e, 409)) void qc.invalidateQueries({ queryKey: authKeys.status });
    },
  });

  const lockedFor = Math.max(0, Math.ceil((lockedUntil - now) / 1000));
  useEffect(() => {
    if (lockedUntil <= Date.now()) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [lockedUntil]);

  if (status.data?.setup_required) return <Navigate to="/setup" replace />;
  if (me.data) return <Navigate to={next} replace />;
  if (status.isPending || me.isPending) return <FullPageSpinner />;

  const onSubmit = (ev: FormEvent) => {
    ev.preventDefault();
    if (!username.trim() || !password || lockedFor > 0) return;
    login.mutate();
  };

  const error = login.isError ? loginErrorMessage(login.error, lockedFor) : null;

  return (
    <AuthShell>
      <Card className="shadow-xl shadow-black/5">
        <CardHeader className="space-y-2">
          <div className="flex h-10 w-10 items-center justify-center rounded-full bg-accent text-accent-foreground">
            <LockKeyhole className="h-5 w-5" aria-hidden="true" />
          </div>
          <CardTitle className="text-xl">Sign in to Kino</CardTitle>
          <CardDescription>Use the admin account for this Kino server.</CardDescription>
        </CardHeader>
        <CardContent>
          <form className="space-y-4" onSubmit={onSubmit} noValidate>
            <div className="space-y-2">
              <Label htmlFor="username">Username</Label>
              <Input
                id="username"
                name="username"
                autoComplete="username"
                autoCapitalize="none"
                spellCheck={false}
                dir="auto"
                autoFocus
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                aria-invalid={isApiError(login.error, 401) || undefined}
                required
              />
            </div>
            {error && (
              <div
                role="alert"
                className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive"
              >
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                <span>{error}</span>
              </div>
            )}
            <Button
              type="submit"
              className="w-full"
              disabled={login.isPending || lockedFor > 0 || !username.trim() || !password}
            >
              {login.isPending && <Loader2 className="animate-spin" aria-hidden="true" />}
              {lockedFor > 0 ? `Locked (${formatDuration(lockedFor)})` : "Sign in"}
            </Button>
          </form>
        </CardContent>
      </Card>
      <p className="mt-6 text-center text-xs text-muted-foreground">
        Reached Kino through Cloudflare Access? You are signed in automatically.
      </p>
    </AuthShell>
  );
}
