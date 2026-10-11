import { cn } from "@/lib/utils";

/** Kino mark: a drone's camera eye. */
export function Logo({ className, withName = true }: { className?: string; withName?: boolean }) {
  return (
    <span className={cn("inline-flex items-center gap-2 font-semibold tracking-tight", className)}>
      <svg viewBox="0 0 32 32" className="h-7 w-7 shrink-0" aria-hidden="true">
        <rect width="32" height="32" rx="8" className="fill-foreground" />
        <circle cx="16" cy="16" r="8.5" fill="none" strokeWidth="2.5" className="stroke-primary" />
        <circle cx="16" cy="16" r="3.5" className="fill-primary" />
      </svg>
      {withName && <span className="text-lg">Kino</span>}
    </span>
  );
}
