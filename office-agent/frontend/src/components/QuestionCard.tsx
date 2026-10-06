import { useState } from "react";
import type { LogItem } from "../state/reducer";
import type { QuestionSpec } from "../types/wire";
import { ArrowRightIcon, CheckIcon, ChevronRightIcon, CloseIcon, PencilIcon } from "./icons";

type QuestionItem = Extract<LogItem, { kind: "question" }>;
/** undefined: not answered yet; null: skipped. */
type Answer = string | null | undefined;

/** ask_user_question's questions, docked above the composer at its width.
 * A single-choice question answers on the click and moves on; a
 * multi-choice one collects ticks until its arrow. Each can be skipped or
 * answered in the user's own words, the pager steps between them, and
 * the answers go back together once every question has one. The close
 * button answers none of them. */
export function QuestionPanel({
  item,
  onAnswer,
}: {
  item: QuestionItem;
  onAnswer: (id: string, answers: (string | null)[] | null) => void;
}) {
  const questions = item.questions;
  const [index, setIndex] = useState(0);
  const [answers, setAnswers] = useState<Answer[]>(() => questions.map(() => undefined));
  const question = questions[index];

  const answer = (value: string | null) => {
    const next = answers.map((a, i) => (i === index ? value : a));
    setAnswers(next);
    const after = [...next.keys()].slice(index + 1).concat([...next.keys()].slice(0, index + 1));
    const open = after.find((i) => next[i] === undefined);
    if (open === undefined) onAnswer(item.id, next.map((a) => a ?? null));
    else setIndex(open);
  };

  if (!question) return null;
  return (
    <div className="mx-auto w-full max-w-[880px] px-4 pb-2">
      <div className="rounded-2xl border border-[var(--border)] bg-[var(--bg)] shadow-sm">
        <div className="flex items-start justify-between gap-3 px-5 pb-1.5 pt-4">
          <div className="min-w-0 text-[15px] font-medium">{question.question}</div>
          <div className="flex shrink-0 items-center gap-1 text-sm text-[var(--muted)]">
            {questions.length > 1 && (
              <>
                <button
                  type="button"
                  aria-label="Previous question"
                  className="rounded-md p-1 hover:bg-[var(--card-bg)] hover:text-[var(--fg)] disabled:opacity-40 disabled:hover:bg-transparent"
                  disabled={index === 0}
                  onClick={() => setIndex(index - 1)}
                >
                  <ChevronRightIcon className="h-4 w-4 rotate-180" />
                </button>
                <span className="tabular-nums">
                  {index + 1} of {questions.length}
                </span>
                <button
                  type="button"
                  aria-label="Next question"
                  className="rounded-md p-1 hover:bg-[var(--card-bg)] hover:text-[var(--fg)] disabled:opacity-40 disabled:hover:bg-transparent"
                  disabled={index === questions.length - 1}
                  onClick={() => setIndex(index + 1)}
                >
                  <ChevronRightIcon className="h-4 w-4" />
                </button>
              </>
            )}
            <button
              type="button"
              aria-label="Close the questions"
              className="ml-2 rounded-md p-1 hover:bg-[var(--card-bg)] hover:text-[var(--fg)]"
              onClick={() => onAnswer(item.id, null)}
            >
              <CloseIcon className="h-4 w-4" />
            </button>
          </div>
        </div>
        {question.multi_select ? (
          <MultiChoice key={index} question={question} previous={answers[index]} onAnswer={answer} />
        ) : (
          <SingleChoice key={index} question={question} previous={answers[index]} onAnswer={answer} />
        )}
      </div>
    </div>
  );
}

function OptionText({ label, description }: { label: string; description: string }) {
  return (
    <span className="min-w-0">
      <span className="block text-sm">{label}</span>
      {description && <span className="block text-xs text-[var(--muted)]">{description}</span>}
    </span>
  );
}

const ROW = "flex w-full items-center gap-3 border-b border-[var(--border)] px-2 py-2.5 text-left";

function SingleChoice({
  question,
  previous,
  onAnswer,
}: {
  question: QuestionSpec;
  previous: Answer;
  onAnswer: (value: string | null) => void;
}) {
  const labels = question.options.map((o) => o.label);
  const typedBefore = typeof previous === "string" && !labels.includes(previous) ? previous : "";
  const [typing, setTyping] = useState(typedBefore !== "");
  const [text, setText] = useState(typedBefore);
  const send = () => text.trim() && onAnswer(text.trim());

  return (
    <div className="px-3 pb-3">
      {question.options.map((option, i) => {
        const chosen = previous === option.label;
        return (
          <button
            key={option.label}
            type="button"
            className={`${ROW} hover:bg-[var(--card-bg)] ${chosen ? "bg-[var(--card-bg)]" : ""}`}
            onClick={() => onAnswer(option.label)}
          >
            <span
              className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-sm ${
                chosen ? "bg-[var(--accent)] text-[var(--accent-fg)]" : "bg-[var(--card-bg)] text-[var(--muted)]"
              }`}
            >
              {i + 1}
            </span>
            <OptionText label={option.label} description={option.description} />
          </button>
        );
      })}
      <div className="flex items-center gap-3 px-2 pt-2.5">
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-[var(--card-bg)] text-[var(--muted)]">
          <PencilIcon className="h-3.5 w-3.5" />
        </span>
        {typing ? (
          <input
            autoFocus
            className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-[var(--muted)]"
            placeholder="Type your own answer"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.nativeEvent.isComposing) send();
              if (e.key === "Escape") setTyping(false);
            }}
          />
        ) : (
          <button
            type="button"
            className="min-w-0 flex-1 text-left text-sm text-[var(--muted)] hover:text-[var(--fg)]"
            onClick={() => setTyping(true)}
          >
            Something else
          </button>
        )}
        {typing && text.trim() ? (
          <SendButton onClick={send} />
        ) : (
          <SkipButton onClick={() => onAnswer(null)} />
        )}
      </div>
    </div>
  );
}

function MultiChoice({
  question,
  previous,
  onAnswer,
}: {
  question: QuestionSpec;
  previous: Answer;
  onAnswer: (value: string | null) => void;
}) {
  const labels = question.options.map((o) => o.label);
  const before = typeof previous === "string" ? previous.split(", ") : [];
  const [picked, setPicked] = useState(() => new Set(before.filter((p) => labels.includes(p))));
  const typedBefore = before.filter((p) => !labels.includes(p)).join(", ");
  const [typing, setTyping] = useState(typedBefore !== "");
  const [text, setText] = useState(typedBefore);
  const own = typing && text.trim() ? [text.trim()] : [];
  const count = picked.size + own.length;
  const submit = () => count > 0 && onAnswer([...labels.filter((l) => picked.has(l)), ...own].join(", "));
  const toggle = (label: string) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      return next;
    });

  return (
    <>
      <div className="px-3 pb-3">
        {question.options.map((option) => (
          <button
            key={option.label}
            type="button"
            className={`${ROW} hover:bg-[var(--card-bg)]`}
            onClick={() => toggle(option.label)}
          >
            <Checkbox checked={picked.has(option.label)} />
            <OptionText label={option.label} description={option.description} />
          </button>
        ))}
        <div className="flex items-center gap-3 px-2 pt-2.5">
          <button type="button" aria-label="Something else" onClick={() => setTyping(!typing)}>
            <Checkbox checked={typing} />
          </button>
          {typing ? (
            <input
              autoFocus
              className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-[var(--muted)]"
              placeholder="Type your own answer"
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.nativeEvent.isComposing) submit();
              }}
            />
          ) : (
            <button
              type="button"
              className="min-w-0 flex-1 text-left text-sm text-[var(--muted)] hover:text-[var(--fg)]"
              onClick={() => setTyping(true)}
            >
              Something else
            </button>
          )}
        </div>
      </div>
      <div className="flex items-center justify-between border-t border-[var(--border)] px-5 py-3">
        <span className="text-sm text-[var(--muted)]">{count} selected</span>
        <div className="flex items-center gap-2">
          <SkipButton onClick={() => onAnswer(null)} />
          <SendButton onClick={submit} disabled={count === 0} />
        </div>
      </div>
    </>
  );
}

function Checkbox({ checked }: { checked: boolean }) {
  return (
    <span
      className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-md border ${
        checked ? "border-[var(--accent)] bg-[var(--accent)] text-[var(--accent-fg)]" : "border-[var(--border)]"
      }`}
    >
      {checked && <CheckIcon className="h-3 w-3" />}
    </span>
  );
}

function SkipButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      className="shrink-0 rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 py-1 text-sm hover:bg-[var(--card-bg)]"
      onClick={onClick}
    >
      Skip
    </button>
  );
}

function SendButton({ onClick, disabled = false }: { onClick: () => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      aria-label="Answer"
      className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[var(--accent)] text-[var(--accent-fg)] disabled:bg-[var(--muted)] disabled:opacity-60"
      disabled={disabled}
      onClick={onClick}
    >
      <ArrowRightIcon className="h-4 w-4" />
    </button>
  );
}

/** An answered ask_user_question in the conversation: each question with
 * the answer it got. */
export function QuestionRecord({ item }: { item: QuestionItem }) {
  return (
    <div className="self-start w-full max-w-[560px] rounded-[14px] border border-[var(--border)] bg-[var(--card-bg)] px-4 py-3 text-sm">
      {item.answers === null ? (
        <>
          {item.questions.map((q, i) => (
            <div key={i} className="text-[var(--muted)]">
              {q.question}
            </div>
          ))}
          <div className="mt-1.5 text-[var(--muted)]">Closed without answering</div>
        </>
      ) : (
        item.questions.map((q, i) => (
          <div key={i} className={i > 0 ? "mt-2.5" : ""}>
            <div className="text-[var(--muted)]">{q.question}</div>
            <div className="mt-0.5">{item.answers?.[i] ?? <span className="text-[var(--muted)]">(skipped)</span>}</div>
          </div>
        ))
      )}
    </div>
  );
}
