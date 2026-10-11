import * as DialogPrimitive from "@radix-ui/react-dialog";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Cloud, KeyRound, LogOut, Menu, X } from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router";
import { Logo } from "@/components/Logo";
import { ThemeToggle } from "@/components/layout/ThemeToggle";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { toast } from "@/components/ui/sonner";
import { authApi, authKeys, reviewCount } from "@/lib/auth";
import { formatBadge } from "@/lib/format";
import { NAV } from "@/lib/nav";
import { useLive } from "@/lib/query";
import { cn } from "@/lib/utils";

function useReviewTotal(): number {
  const q = useLive(["review", "count"], reviewCount);
  return q.data?.total ?? 0;
}

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const pending = useReviewTotal();
  return (
    <nav aria-label="Main" className="flex flex-col gap-0.5">
      {NAV.map(({ to, label, icon: Icon }) => (
        <NavLink
          key={to}
          to={to}
          onClick={onNavigate}
          className={({ isActive }) =>
            cn(
              "group flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
              isActive
                ? "bg-accent text-accent-foreground"
                : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
            )
          }
        >
          <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
          <span className="flex-1">{label}</span>
          {to === "/review" && pending > 0 && (
            <Badge aria-label={`${pending} waiting for review`}>{formatBadge(pending)}</Badge>
          )}
        </NavLink>
      ))}
    </nav>
  );
}

function Account() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const me = useQuery({ queryKey: authKeys.me, queryFn: authApi.me, retry: false, staleTime: 5 * 60_000 });
  const logout = useMutation({
    mutationFn: authApi.logout,
    onSuccess: () => {
      qc.clear();
      navigate("/login", { replace: true });
    },
    onError: (e) => toast.error(`Sign-out failed: ${e.message}`),
  });
  if (!me.data) return null;
  const cloudflare = me.data.method === "cloudflare";
  const Icon = cloudflare ? Cloud : KeyRound;
  return (
    <div className="flex items-center gap-2 rounded-lg border bg-background/60 p-2">
      <Icon className="ms-1 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
      <div className="min-w-0 flex-1 leading-tight">
        <p className="text-xs text-muted-foreground">{cloudflare ? "Cloudflare Access" : "Local login"}</p>
        <p className="truncate text-sm font-medium" dir="auto" title={me.data.user}>
          {me.data.user}
        </p>
      </div>
      {!cloudflare && (
        <Button
          variant="ghost"
          size="icon"
          aria-label="Sign out"
          title="Sign out"
          disabled={logout.isPending}
          onClick={() => logout.mutate()}
        >
          <LogOut />
        </Button>
      )}
    </div>
  );
}

function SidebarBody({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <div className="flex h-full flex-col gap-6 p-4">
      <div className="flex items-center justify-between px-1">
        <Logo />
        {/* In the mobile drawer the top bar already has the toggle; the close button sits here. */}
        {!onNavigate && <ThemeToggle />}
      </div>
      <div className="flex-1 overflow-y-auto">
        <NavLinks onNavigate={onNavigate} />
      </div>
      <Account />
    </div>
  );
}

export function AppLayout() {
  const [open, setOpen] = useState(false);
  const pending = useReviewTotal();
  return (
    <div className="min-h-svh lg:grid lg:grid-cols-[16rem_1fr]">
      {/* Desktop sidebar */}
      <aside className="sticky top-0 hidden h-svh border-e bg-sidebar text-sidebar-foreground lg:block">
        <SidebarBody />
      </aside>

      {/* Mobile top bar + drawer */}
      <header className="sticky top-0 z-30 flex h-14 items-center gap-2 border-b bg-background/85 px-4 backdrop-blur lg:hidden">
        <DialogPrimitive.Root open={open} onOpenChange={setOpen}>
          <DialogPrimitive.Trigger asChild>
            <Button variant="ghost" size="icon" className="relative -ms-2" aria-label="Open menu">
              <Menu />
              {pending > 0 && (
                <span className="absolute end-1.5 top-1.5 h-2 w-2 rounded-full bg-primary" aria-hidden="true" />
              )}
            </Button>
          </DialogPrimitive.Trigger>
          <DialogPrimitive.Portal>
            <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/50 data-[state=open]:animate-in data-[state=open]:fade-in-0" />
            <DialogPrimitive.Content
              className="fixed inset-y-0 start-0 z-50 w-72 max-w-[85vw] border-e bg-sidebar text-sidebar-foreground shadow-xl data-[state=open]:animate-in data-[state=open]:slide-in-from-left rtl:data-[state=open]:slide-in-from-right"
              aria-describedby={undefined}
            >
              <DialogPrimitive.Title className="sr-only">Navigation</DialogPrimitive.Title>
              <DialogPrimitive.Close asChild>
                <Button variant="ghost" size="icon" className="absolute end-2 top-2 z-10" aria-label="Close menu">
                  <X />
                </Button>
              </DialogPrimitive.Close>
              <SidebarBody onNavigate={() => setOpen(false)} />
            </DialogPrimitive.Content>
          </DialogPrimitive.Portal>
        </DialogPrimitive.Root>
        <Logo />
        <div className="ms-auto">
          <ThemeToggle />
        </div>
      </header>

      <main className="min-w-0 px-4 py-6 sm:px-6 lg:px-10 lg:py-8">
        <Outlet />
      </main>
    </div>
  );
}
