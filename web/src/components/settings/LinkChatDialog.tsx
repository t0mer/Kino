import { useQueryClient } from "@tanstack/react-query";
import { Check, Copy, Loader2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toast } from "@/components/ui/sonner";
import { isApiError } from "@/lib/api";
import { type LinkStart, type TelegramChat, telegramApi, telegramKeys } from "@/lib/telegram";

type Phase =
  | { kind: "starting" }
  | { kind: "waiting"; link: LinkStart }
  | { kind: "linked"; chat: TelegramChat }
  | { kind: "expired" }
  | { kind: "error"; message: string };

const BACKOFF_MS = 1000;
/** Pause between polls so a server that answers "pending" instantly is not hammered. */
const PENDING_GAP_MS = 250;
const wait = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve) => {
    const t = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => {
      clearTimeout(t);
      resolve();
    });
  });

/** Starts a link code on open and long-polls until a chat sends it (or it expires). */
export function LinkChatDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  const qc = useQueryClient();
  const [phase, setPhase] = useState<Phase>({ kind: "starting" });
  const [attempt, setAttempt] = useState(0);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!open) return;
    const ctl = new AbortController();
    setPhase({ kind: "starting" });
    setCopied(false);
    (async () => {
      let link: LinkStart;
      try {
        link = await telegramApi.startLink();
      } catch (e) {
        if (!ctl.signal.aborted) setPhase({ kind: "error", message: (e as Error).message });
        return;
      }
      if (ctl.signal.aborted) return;
      setPhase({ kind: "waiting", link });
      while (!ctl.signal.aborted) {
        try {
          const r = await telegramApi.pollLink(link.code, ctl.signal);
          if (ctl.signal.aborted) return;
          if (r.status === "linked" && r.chat) {
            setPhase({ kind: "linked", chat: r.chat });
            qc.invalidateQueries({ queryKey: telegramKeys.chats });
            toast.success(`Linked ${r.chat.title}`);
            return;
          }
          if (r.status === "expired") {
            setPhase({ kind: "expired" });
            return;
          }
          await wait(PENDING_GAP_MS, ctl.signal);
        } catch (e) {
          if (ctl.signal.aborted) return;
          // A definite answer (404/409/...) ends the loop; only network errors retry.
          if (isApiError(e) && e.status !== 0) {
            setPhase({ kind: "error", message: e.message });
            return;
          }
          await wait(BACKOFF_MS, ctl.signal);
        }
      }
    })();
    return () => ctl.abort();
  }, [open, attempt, qc]);

  // Close shortly after success so the user sees which chat was linked.
  useEffect(() => {
    if (phase.kind !== "linked") return;
    const t = setTimeout(() => onOpenChange(false), 1500);
    return () => clearTimeout(t);
  }, [phase.kind, onOpenChange]);

  const copy = useCallback(async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      toast.error("Could not copy the code");
    }
  }, []);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Link a Telegram chat</DialogTitle>
          <DialogDescription>Prove you control the chat by sending Kino&apos;s bot a one-time code.</DialogDescription>
        </DialogHeader>

        {phase.kind === "starting" && (
          <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> Generating a code…
          </p>
        )}

        {phase.kind === "waiting" && (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <code
                data-testid="link-code"
                className="flex-1 rounded-md border bg-muted px-3 py-2 text-center font-mono text-2xl tracking-widest"
              >
                {phase.link.code}
              </code>
              <Button
                type="button"
                variant="outline"
                size="icon"
                aria-label="Copy code"
                onClick={() => copy(phase.link.code)}
              >
                {copied ? <Check /> : <Copy />}
              </Button>
            </div>
            {phase.link.bot_username === null && phase.link.instructions ? (
              <p className="text-sm">{phase.link.instructions}</p>
            ) : (
              <p className="text-sm">
                Send <code className="font-mono">/start {phase.link.code}</code> to{" "}
                <strong>{phase.link.bot_username ? `@${phase.link.bot_username}` : "your bot"}</strong> (or add the
                bot to the group and post the code there).
              </p>
            )}
            <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> Waiting for message…
            </p>
          </div>
        )}

        {phase.kind === "linked" && (
          <p role="status" className="flex items-center gap-2 text-sm">
            <Check className="h-4 w-4 text-primary" aria-hidden="true" /> Linked{" "}
            <strong dir="auto">{phase.chat.title}</strong> ({phase.chat.type})
          </p>
        )}

        {phase.kind === "expired" && (
          <div className="space-y-2">
            <p role="status" className="text-sm text-muted-foreground">
              This code expired.
            </p>
            <Button type="button" onClick={() => setAttempt((n) => n + 1)}>
              Generate new code
            </Button>
          </div>
        )}

        {phase.kind === "error" && (
          <div className="space-y-2">
            <p role="alert" className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {phase.message}
            </p>
            <Button type="button" variant="outline" onClick={() => setAttempt((n) => n + 1)}>
              Try again
            </Button>
          </div>
        )}

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            {phase.kind === "linked" ? "Done" : "Cancel"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
