import { useState } from "react";
import type { LogItem } from "../state/reducer";
import { CheckIcon, CloseIcon, PencilIcon } from "./icons";

interface QuestionCardProps {
  item: Extract<LogItem, { kind: "question" }>;
  onAnswer: (id: string, answer: string) => void;
}

/** ask_user_question's own card -- lets the agent ask a clarifying
 * question with clickable choices instead of only asking in prose (the
 * same idea as Claude Code's own AskUserQuestion tool). Single-select
 * answers on the first click; multi-select accumulates a selection and
 * needs an explicit Confirm, since there's no single click that means
 * "done choosing." "Something else" always stays available -- the
 * listed options narrow the likely answers, they don't have to be
 * exhaustive -- and the header's close button skips the question with
 * an empty answer rather than forcing a choice. */
/** Card body/option rows rest on `--card-bg` (this theme's near-white
 * surface) with `--panel-bg` (the darker gray) reserved for hover/
 * selected states and the option-number badge -- not the other way
 * around. Getting this backwards (gray card body, near-white badge) was
 * the actual bug an earlier pass shipped with: everything read as a flat
 * gray-on-gray card instead of a crisp white card with a visibly gray
 * number chip. */
export function QuestionCard({ item, onAnswer }: QuestionCardProps) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [customText, setCustomText] = useState("");
  const [showCustomInput, setShowCustomInput] = useState(false);

  if (item.status === "answered") {
    return (
      <div className="self-start w-full max-w-[420px] rounded-[14px] border border-[var(--border)] bg-[var(--card-bg)] px-4 py-3 text-sm">
        {item.header && (
          <div className="mb-0.5 text-xs font-medium uppercase tracking-wide text-[var(--muted)]">
            {item.header}
          </div>
        )}
        <div className="text-[var(--muted)]">{item.question}</div>
        <div className="mt-1.5">{item.answer || <span className="text-[var(--muted)]">(skipped)</span>}</div>
      </div>
    );
  }

  const toggleOption = (option: string) => {
    if (!item.multiSelect) {
      onAnswer(item.id, option);
      return;
    }
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(option)) next.delete(option);
      else next.add(option);
      return next;
    });
  };

  const submitCustom = () => {
    const text = customText.trim();
    if (!text) return;
    onAnswer(item.id, text);
  };

  const submitSelection = () => onAnswer(item.id, [...selected].join(", "));

  return (
    <div className="self-start w-full max-w-[420px] rounded-[14px] border border-[var(--border)] bg-[var(--card-bg)] shadow-[var(--shadow)]">
      <div className="flex items-start justify-between gap-2 px-4 pb-2 pt-3.5">
        <div className="min-w-0">
          {item.header && (
            <div className="mb-0.5 text-xs font-medium uppercase tracking-wide text-[var(--muted)]">
              {item.header}
            </div>
          )}
          <div className="text-sm font-medium">{item.question}</div>
        </div>
        <button
          type="button"
          aria-label="Skip this question"
          className="shrink-0 rounded-md p-1 text-[var(--muted)] hover:bg-[var(--panel-bg)] hover:text-[var(--fg)]"
          onClick={() => onAnswer(item.id, "")}
        >
          <CloseIcon className="h-4 w-4" />
        </button>
      </div>
      <div className="border-t border-[var(--border)]">
        {item.options.map((option, index) => {
          const isSelected = item.multiSelect && selected.has(option);
          return (
            <button
              key={option}
              type="button"
              className={`flex w-full items-center gap-3 border-b border-[var(--border)] px-4 py-2.5 text-left text-sm last:border-b-0 hover:bg-[var(--panel-bg)] ${
                isSelected ? "bg-[var(--panel-bg)]" : ""
              }`}
              onClick={() => toggleOption(option)}
            >
              {item.multiSelect ? (
                <span
                  className={`flex h-5 w-5 shrink-0 items-center justify-center rounded border ${
                    isSelected
                      ? "border-[var(--accent)] bg-[var(--accent)] text-[var(--accent-fg)]"
                      : "border-[var(--border)]"
                  }`}
                >
                  {isSelected && <CheckIcon className="h-3 w-3" />}
                </span>
              ) : (
                <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-[var(--panel-bg)] text-xs text-[var(--muted)]">
                  {index + 1}
                </span>
              )}
              <span className="min-w-0 truncate">{option}</span>
            </button>
          );
        })}
        {showCustomInput ? (
          <div className="flex items-center gap-2 px-4 py-2.5">
            <PencilIcon className="h-3.5 w-3.5 shrink-0 text-[var(--muted)]" />
            <input
              autoFocus
              className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-[var(--muted)]"
              placeholder="Type your own answer..."
              value={customText}
              onChange={(e) => setCustomText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") submitCustom();
                if (e.key === "Escape") {
                  setShowCustomInput(false);
                  setCustomText("");
                }
              }}
            />
          </div>
        ) : (
          <button
            type="button"
            className="flex w-full items-center gap-3 px-4 py-2.5 text-left text-sm text-[var(--muted)] hover:bg-[var(--panel-bg)] hover:text-[var(--fg)]"
            onClick={() => setShowCustomInput(true)}
          >
            <PencilIcon className="h-3.5 w-3.5 shrink-0" />
            Something else
          </button>
        )}
      </div>
      {(item.multiSelect || showCustomInput) && (
        <div className="flex justify-end gap-2 border-t border-[var(--border)] px-4 py-2.5">
          {showCustomInput && (
            <button
              type="button"
              className="rounded-md border border-[var(--border)] px-3 py-1.5 text-sm"
              onClick={() => {
                setShowCustomInput(false);
                setCustomText("");
              }}
            >
              Cancel
            </button>
          )}
          <button
            type="button"
            className="rounded-md bg-[var(--accent)] px-3 py-1.5 text-sm text-[var(--accent-fg)] disabled:opacity-40"
            disabled={showCustomInput ? !customText.trim() : selected.size === 0}
            onClick={showCustomInput ? submitCustom : submitSelection}
          >
            {showCustomInput ? "Send" : "Confirm"}
          </button>
        </div>
      )}
    </div>
  );
}
