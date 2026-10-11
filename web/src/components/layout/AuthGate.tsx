import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { Navigate, Outlet, useLocation, useNavigate } from "react-router";
import { FullPageSpinner } from "@/components/FullPageSpinner";
import { Button } from "@/components/ui/button";
import { isApiError, setUnauthorizedHandler } from "@/lib/api";
import { authApi, authKeys } from "@/lib/auth";

const PUBLIC = ["/login", "/setup"];

/** Registers the global 401 handler: any API call that returns 401 sends the user to /login. */
export function UnauthorizedRedirect() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  useEffect(() => {
    setUnauthorizedHandler(() => {
      const { pathname, search } = window.location;
      if (PUBLIC.some((p) => pathname.startsWith(p))) return;
      navigate(`/login?next=${encodeURIComponent(pathname + search)}`, { replace: true });
      qc.clear();
    });
    return () => setUnauthorizedHandler(null);
  }, [navigate, qc]);
  return null;
}

/** Renders the app only for an authenticated user (Cloudflare Access or local session). */
export function RequireAuth() {
  const location = useLocation();
  const me = useQuery({ queryKey: authKeys.me, queryFn: authApi.me, retry: false, staleTime: 5 * 60_000 });

  if (me.isPending) return <FullPageSpinner />;
  if (me.isError) {
    if (isApiError(me.error, 401)) {
      const next = location.pathname + location.search;
      return <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />;
    }
    return (
      <div className="flex min-h-svh flex-col items-center justify-center gap-3 p-6 text-center">
        <p className="text-lg font-medium">Kino is not responding</p>
        <p className="max-w-sm text-sm text-muted-foreground">{me.error.message}</p>
        <Button variant="outline" onClick={() => void me.refetch()}>
          Try again
        </Button>
      </div>
    );
  }
  return <Outlet />;
}
