import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Check, Loader2, ShieldCheck } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Navigate, useNavigate } from "react-router";
import { FullPageSpinner } from "@/components/FullPageSpinner";
import { AuthShell } from "@/components/layout/AuthShell";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "@/components/ui/sonner";
import { ApiError } from "@/lib/api";
import { MIN_SETUP_PASSWORD, authApi, authKeys } from "@/lib/auth";
import { cn } from "@/lib/utils";
import { usePageTitle } from "@/lib/usePageTitle";

/** Client-side checks for the first-run password; null when it is acceptable. */
export function setupPasswordError(password: string, confirm: string): string | null {
  if ([...password].length < MIN_SETUP_PASSWORD)
    return `Use at least ${MIN_SETUP_PASSWORD} characters.`;
  if (password !== confirm) return "The passwords do not match.";
  return null;
}

function setupErrorMessage(e: unknown): string {
  if (!(e instanceof ApiError)) return "Something went wrong. Try again.";
  if (e.status === 403 && /local network/i.test(e.detail))
    return "First-run setup only works from your local network, not through the tunnel or a proxy.";
  if (e.status === 403) return "The security cookie is missing or blocked. Reload the page and try again.";
  if (e.status === 0) return "Cannot reach the Kino server. Check that it is running.";
  return e.detail || `Setup failed (HTTP ${e.status}).`;
}

export function SetupPage() {
  usePageTitle("First-run setup");
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [touched, setTouched] = useState(false);
  const status = useQuery({ queryKey: authKeys.status, queryFn: authApi.status, retry: false });

  const setup = useMutation({
    mutationFn: () => authApi.setup(password),
    onSuccess: (me) => {
      qc.setQueryData(authKeys.me, me);
      qc.setQueryData(authKeys.status, { setup_required: false });
      toast.success(`Admin password set. Signed in as ${me.user}.`);
      navigate("/", { replace: true });
    },
    onError: (e) => {
      if (e instanceof ApiError && e.status === 409) {
        void qc.invalidateQueries({ queryKey: authKeys.status });
        navigate("/login", { replace: true });
      }
    },
  });

  if (status.isPending) return <FullPageSpinner />;
  if (status.data && !status.data.setup_required) return <Navigate to="/login" replace />;

  const localError = setupPasswordError(password, confirm);
  const longEnough = [...password].length >= MIN_SETUP_PASSWORD;
  const matches = password.length > 0 && password === confirm;
  const onSubmit = (ev: FormEvent) => {
    ev.preventDefault();
    setTouched(true);
    if (!localError) setup.mutate();
  };
  const shownError = setup.isError
    ? setupErrorMessage(setup.error)
    : touched
      ? localError
      : status.isError
        ? "Cannot reach the Kino server."
        : null;

  return (
    <AuthShell>
      <Card className="shadow-xl shadow-black/5">
        <CardHeader className="space-y-2">
          <div className="flex h-10 w-10 items-center justify-center rounded-full bg-accent text-accent-foreground">
            <ShieldCheck className="h-5 w-5" aria-hidden="true" />
          </div>
          <CardTitle className="text-xl">Welcome to Kino</CardTitle>
          <CardDescription>
            Set the admin password to finish setting up. You can only do this once, from your local
            network.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form className="space-y-4" onSubmit={onSubmit} noValidate>
            <div className="space-y-2">
              <Label htmlFor="new-password">New password</Label>
              <Input
                id="new-password"
                type="password"
                autoComplete="new-password"
                autoFocus
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                aria-describedby="password-rules"
                aria-invalid={(touched && !longEnough) || undefined}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="confirm-password">Confirm password</Label>
              <Input
                id="confirm-password"
                type="password"
                autoComplete="new-password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                aria-invalid={(touched && !matches) || undefined}
              />
            </div>
            <ul id="password-rules" className="space-y-1 text-xs">
              {[
                [longEnough, `At least ${MIN_SETUP_PASSWORD} characters`],
                [matches, "Both passwords match"],
              ].map(([ok, text]) => (
                <li
                  key={String(text)}
                  className={cn("flex items-center gap-1.5", ok ? "text-success" : "text-muted-foreground")}
                >
                  <Check className={cn("h-3.5 w-3.5", !ok && "opacity-30")} aria-hidden="true" />
                  {text}
                </li>
              ))}
            </ul>
            {shownError && (
              <div
                role="alert"
                className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive"
              >
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                <span>{shownError}</span>
              </div>
            )}
            <Button type="submit" className="w-full" disabled={setup.isPending}>
              {setup.isPending && <Loader2 className="animate-spin" aria-hidden="true" />}
              Set password and sign in
            </Button>
          </form>
        </CardContent>
      </Card>
    </AuthShell>
  );
}
