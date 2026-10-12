import { useQuery } from "@tanstack/react-query";
import { Loader2, RefreshCw, Search, Users, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { type Chat, chatLabel, settingsApi, settingsKeys } from "@/lib/settings";

export const WATCHED_REASON = "watched group - would loop";

/** id -> name for every chat OpenWA lists (best effort; empty when OpenWA is unreachable). */
export function useChatNames(): Map<string, string | null> {
  const q = useQuery({
    queryKey: settingsKeys.chats(""),
    queryFn: () => settingsApi.chats(""),
    staleTime: 5 * 60_000,
    retry: false,
  });
  return useMemo(() => new Map((q.data ?? []).map((c) => [c.id, c.name])), [q.data]);
}

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function ChatList({ query, onPick }: { query: string; onPick: (c: Chat) => void }) {
  const q = useQuery({
    queryKey: settingsKeys.chats(query),
    queryFn: () => settingsApi.chats(query),
    retry: false,
  });
  if (q.isPending)
    return (
      <div className="space-y-2" aria-busy="true" aria-label="Loading chats">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-10" />
        ))}
      </div>
    );
  if (q.isError)
    return (
      <div className="space-y-2 py-4 text-center">
        <p className="text-sm font-medium">Could not load chats</p>
        <p className="text-sm text-muted-foreground">{q.error.message}</p>
        <Button variant="outline" size="sm" onClick={() => q.refetch()}>
          <RefreshCw /> Retry
        </Button>
      </div>
    );
  if (q.data.length === 0) return <p className="py-6 text-center text-sm text-muted-foreground">No chats match.</p>;
  return (
    <ul className="max-h-72 space-y-1 overflow-y-auto" aria-label="Chats">
      {q.data.map((c) => (
        <li key={c.id}>
          <button
            type="button"
            disabled={!c.selectable}
            onClick={() => onPick(c)}
            className="flex w-full items-center gap-3 rounded-md px-3 py-2 text-start text-sm hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60 disabled:hover:bg-transparent"
          >
            {c.is_group && <Users className="h-4 w-4 shrink-0 text-muted-foreground" aria-label="Group" />}
            <span className="min-w-0 flex-1">
              <span className="block truncate" dir="auto">
                {c.name?.trim() || c.id}
              </span>
              {c.name?.trim() && <span className="block truncate text-xs text-muted-foreground">{c.id}</span>}
              {!c.selectable && <span className="block text-xs text-destructive">{WATCHED_REASON}</span>}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}

/** A destination field: shows the chosen chat and opens a searchable dialog to change it. */
export function ChatPicker({
  id,
  label,
  value,
  onChange,
  emptyLabel = "Not set",
  names,
  error,
}: {
  id: string;
  label: string;
  value: string | null;
  onChange: (id: string | null) => void;
  emptyLabel?: string;
  names: Map<string, string | null>;
  error?: string;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const debounced = useDebounced(text.trim(), 300);

  return (
    <div className="flex items-center gap-2">
      <Button
        id={id}
        type="button"
        variant="outline"
        className="min-w-0 flex-1 justify-start"
        aria-label={`${label}: ${value ? chatLabel(value, names) : emptyLabel}. Change`}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : undefined}
        onClick={() => {
          setText("");
          setOpen(true);
        }}
      >
        <span className="truncate" dir="auto">
          {value ? chatLabel(value, names) : <span className="text-muted-foreground">{emptyLabel}</span>}
        </span>
      </Button>
      {value && (
        <Button type="button" variant="ghost" size="icon" aria-label={`Clear ${label}`} onClick={() => onChange(null)}>
          <X />
        </Button>
      )}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{label}</DialogTitle>
            <DialogDescription>Contacts and groups visible to the Kino WhatsApp session.</DialogDescription>
          </DialogHeader>
          <div className="relative">
            <Search className="pointer-events-none absolute start-3 top-2.5 h-4 w-4 text-muted-foreground" aria-hidden="true" />
            <Input
              autoFocus
              dir="auto"
              className="ps-9"
              placeholder="Search chats"
              aria-label="Search chats"
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
            {text.trim() !== debounced && (
              <Loader2 className="absolute end-3 top-2.5 h-4 w-4 animate-spin text-muted-foreground" aria-hidden="true" />
            )}
          </div>
          <ChatList
            query={debounced}
            onPick={(c) => {
              onChange(c.id);
              setOpen(false);
            }}
          />
        </DialogContent>
      </Dialog>
    </div>
  );
}
