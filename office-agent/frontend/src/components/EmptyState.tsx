interface Suggestion {
  label: string;
  prompt: string;
}

/** Four suggestions, each exercising a different real built-in tool
 * category (write_docx, write_xlsx, write_pptx, list_files) -- grounded
 * in what coscribe actually does, not generic placeholder copy. Each
 * prompt is a complete, self-contained request (not "[describe your
 * idea]" fill-in-the-blank text), so it's sensible to send exactly as
 * written -- clicking one runs it immediately, same convention
 * claude.ai/ChatGPT's own homepage suggestion chips use. Any write is
 * still approval-gated by the normal tool flow, so clicking one of the
 * document/spreadsheet/deck suggestions can't silently create a file. */
const SUGGESTIONS: Suggestion[] = [
  {
    label: "Draft a project brief",
    prompt: "Write a one-page project brief as a Word document, with sections for goals, scope, and timeline.",
  },
  {
    label: "Build a budget tracker",
    prompt: "Create an Excel budget tracker with categories, monthly amounts, and a running-total formula.",
  },
  {
    label: "Outline a slide deck",
    prompt: "Draft a 5-slide pitch deck outline for a new product idea.",
  },
  {
    label: "See what's in my workspace",
    prompt: "List the files in my workspace and summarize what each one is.",
  },
];

interface EmptyStateProps {
  onSuggestion: (prompt: string) => void;
}

/** Local hour, not UTC -- a thread opened at 8am should say "morning"
 * regardless of what timezone the backend or server clock is in. */
function greetingForHour(hour: number): string {
  if (hour < 5) return "Good night";
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

/** Shown in place of the message list on a brand-new thread -- otherwise
 * a first-time user lands on an entirely blank scroll area with no
 * indication of what this app can do beyond a bare "Type / for commands"
 * placeholder. Vertically centered in the same scroll region ChatLog's
 * message list otherwise fills, so it never fights the composer for
 * space. */
export function EmptyState({ onSuggestion }: EmptyStateProps) {
  const greeting = greetingForHour(new Date().getHours());
  return (
    <div className="flex h-full flex-col items-center justify-center gap-6 px-4 text-center">
      <div className="flex flex-col gap-1.5">
        <h1 className="gradient-text text-xl font-semibold">{greeting}</h1>
        <p className="max-w-sm text-sm text-[var(--muted)]">
          coscribe is a local office assistant. Reads and writes real Word, Excel, and PowerPoint files, works
          with the files already in your workspace, and can save what it just did as a workflow to run again
          later.
        </p>
      </div>
      <div className="grid w-full max-w-lg grid-cols-1 gap-2 sm:grid-cols-2">
        {SUGGESTIONS.map((s) => (
          <button
            key={s.label}
            type="button"
            onClick={() => onSuggestion(s.prompt)}
            className="rounded-xl border border-[var(--border)] px-3.5 py-2.5 text-left text-sm hover:bg-[var(--card-bg)]"
          >
            {s.label}
          </button>
        ))}
      </div>
    </div>
  );
}
