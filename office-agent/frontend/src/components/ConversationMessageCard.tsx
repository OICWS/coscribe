import { lazy, Suspense, useState } from "react";
import { ChevronRightIcon, MessageCircleIcon } from "./icons";

const Markdown = lazy(() => import("./Markdown").then((m) => ({ default: m.Markdown })));

// Mirrors tools/conversations.py's format_incoming.
export const CONVERSATION_MESSAGE_PREFIX = "[Message from another conversation]";
const HEADER_RE = /^\[Message from another conversation\] from "(.*?)" \(conversation ([\w-]+)\):\n\n/s;
const NOTE_RE = /\n\n\(This came from another conversation, not from the user\.[^)]*\)\s*$/s;

interface IncomingMessage {
  from: string;
  body: string;
}

function parseMessages(text: string): IncomingMessage[] {
  return text
    .replace(NOTE_RE, "")
    .split("\n\n---\n\n")
    .map((block) => {
      const match = HEADER_RE.exec(block);
      return match ? { from: match[1], body: block.slice(match[0].length).trim() } : null;
    })
    .filter((m): m is IncomingMessage => m !== null);
}

/** Stands in for a message another conversation sent this one -- its assistant
 * wrote it, not the user. */
export function ConversationMessageCard({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const messages = parseMessages(text);
  const senders = [...new Set(messages.map((m) => m.from))];
  return (
    <div className="rounded-xl border border-[var(--border)]">
      <button
        type="button"
        aria-expanded={open}
        className="flex w-full items-center gap-2.5 rounded-xl px-4 py-2.5 text-left text-sm hover:bg-[var(--card-bg)]"
        onClick={() => setOpen((v) => !v)}
      >
        <MessageCircleIcon className="h-4 w-4 shrink-0 text-[var(--muted)]" />
        <span className="min-w-0 flex-1 truncate">
          {messages.length > 0 ? (
            <>
              Message from another conversation:{" "}
              <span className="text-[var(--muted)]">{senders.join(", ")}</span>
            </>
          ) : (
            "Message from another conversation"
          )}
        </span>
        <ChevronRightIcon
          className={`h-4 w-4 shrink-0 text-[var(--muted)] transition-transform motion-reduce:transition-none ${open ? "rotate-90" : ""}`}
        />
      </button>
      {open && (
        <div className="flex max-h-96 flex-col gap-3 overflow-y-auto border-t border-[var(--border)] px-4 py-3 text-sm [overflow-wrap:anywhere]">
          {(messages.length > 0 ? messages : [{ from: "", body: text }]).map((message, index) => (
            <div key={index}>
              {messages.length > 1 && <div className="mb-1 text-xs text-[var(--muted)]">{message.from}</div>}
              <Suspense fallback={<div className="whitespace-pre-wrap">{message.body}</div>}>
                <Markdown text={message.body} />
              </Suspense>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
