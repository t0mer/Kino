/** Display helpers for dates, counts and durations. */

/**
 * Parse an API timestamp. The backend stores naive UTC datetimes, so strings without an offset
 * (`2026-10-11T08:30:00`) are read as UTC rather than local time.
 */
export function parseApiDate(value: string | number | Date | null | undefined): Date | null {
  if (value === null || value === undefined || value === "") return null;
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
  if (typeof value === "number") return new Date(value);
  const hasZone = /([zZ]|[+-]\d{2}:?\d{2})$/.test(value);
  const isDateTime = value.includes("T") || value.includes(" ");
  const d = new Date(isDateTime && !hasZone ? `${value.replace(" ", "T")}Z` : value);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function formatDate(value: Parameters<typeof parseApiDate>[0], locale?: string): string {
  const d = parseApiDate(value);
  return d
    ? new Intl.DateTimeFormat(locale, { year: "numeric", month: "short", day: "numeric" }).format(d)
    : "—";
}

export function formatDateTime(
  value: Parameters<typeof parseApiDate>[0],
  locale?: string,
  timeZone?: string,
): string {
  const d = parseApiDate(value);
  return d
    ? new Intl.DateTimeFormat(locale, {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        timeZone,
      }).format(d)
    : "—";
}

const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 365 * 24 * 3600],
  ["month", 30 * 24 * 3600],
  ["week", 7 * 24 * 3600],
  ["day", 24 * 3600],
  ["hour", 3600],
  ["minute", 60],
];

/** "5 minutes ago", "in 2 hours", "just now". */
export function formatRelative(
  value: Parameters<typeof parseApiDate>[0],
  now: Date = new Date(),
  locale?: string,
): string {
  const d = parseApiDate(value);
  if (!d) return "—";
  const diff = (d.getTime() - now.getTime()) / 1000;
  const abs = Math.abs(diff);
  if (abs < 45) return "just now";
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  for (const [unit, secs] of UNITS) {
    if (abs >= secs) return rtf.format(Math.round(diff / secs), unit);
  }
  return rtf.format(Math.round(diff / 60), "minute");
}

/** Compact badge count: 0..99, then "99+". */
export function formatBadge(n: number, max = 99): string {
  return n > max ? `${max}+` : String(Math.max(0, Math.trunc(n)));
}

/** "1h 5m", "3m 20s", "42s" (for lockout waits and durations). */
export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  if (h) return m ? `${h}h ${m}m` : `${h}h`;
  if (m) return r ? `${m}m ${r}s` : `${m}m`;
  return `${r}s`;
}
