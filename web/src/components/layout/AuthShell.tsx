import type { ReactNode } from "react";
import { Logo } from "@/components/Logo";
import { ThemeToggle } from "@/components/layout/ThemeToggle";

/** Centered card frame for Login and Setup, with viewfinder corner marks. */
export function AuthShell({ children }: { children: ReactNode }) {
  return (
    <div className="relative flex min-h-svh flex-col overflow-hidden bg-background">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 bg-[radial-gradient(60rem_30rem_at_50%_-10%,hsl(var(--primary)/0.14),transparent_70%)]"
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 opacity-[0.35] [background-image:linear-gradient(hsl(var(--border))_1px,transparent_1px),linear-gradient(90deg,hsl(var(--border))_1px,transparent_1px)] [background-size:48px_48px] [mask-image:radial-gradient(ellipse_at_center,black_10%,transparent_65%)]"
      />
      <header className="relative flex items-center justify-between px-4 py-4 sm:px-6">
        <Logo />
        <ThemeToggle />
      </header>
      <main className="relative flex flex-1 items-center justify-center px-4 pb-16">
        <div className="relative w-full max-w-sm">
          {(["start-0 top-0 border-s-2 border-t-2", "end-0 top-0 border-e-2 border-t-2", "start-0 bottom-0 border-s-2 border-b-2", "end-0 bottom-0 border-e-2 border-b-2"] as const).map((c) => (
            <span
              key={c}
              aria-hidden="true"
              className={`absolute -m-3 h-5 w-5 rounded-[3px] border-primary/70 ${c}`}
            />
          ))}
          {children}
        </div>
      </main>
    </div>
  );
}
