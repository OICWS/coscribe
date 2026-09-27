import { useCallback, useEffect, useRef, useState } from "react";
import { onFindStep } from "../lib/menuCommands";
import { ArrowDownIcon, ArrowUpIcon, CloseIcon } from "./icons";

const ALL = "coscribe-find";
const CURRENT = "coscribe-find-current";

function chatLog(): HTMLElement | null {
  return document.querySelector<HTMLElement>('[data-testid="chat-log"]');
}

/** Every case-insensitive occurrence of `query` in the chat's text. A
 * match split across two elements (half bold, say) isn't found. */
function findRanges(root: HTMLElement, query: string): Range[] {
  const needle = query.toLocaleLowerCase();
  const ranges: Range[] = [];
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = (node.nodeValue ?? "").toLocaleLowerCase();
    for (let at = text.indexOf(needle); at !== -1; at = text.indexOf(needle, at + needle.length)) {
      const range = document.createRange();
      range.setStart(node, at);
      range.setEnd(node, at + needle.length);
      ranges.push(range);
    }
  }
  return ranges;
}

function clearHighlights() {
  CSS.highlights.delete(ALL);
  CSS.highlights.delete(CURRENT);
}

/** Find in the conversation: matches are highlighted in place without
 * touching the chat's own DOM (the CSS Custom Highlight API), and follow
 * the log as a reply streams in. */
export function FindInPage({ focusKey, onClose }: { focusKey: number; onClose: () => void }) {
  const [query, setQuery] = useState("");
  const [ranges, setRanges] = useState<Range[]>([]);
  const [index, setIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, [focusKey]);

  const search = useCallback(() => {
    const root = chatLog();
    setRanges(root && query ? findRanges(root, query) : []);
  }, [query]);

  useEffect(() => {
    const timer = window.setTimeout(search, 80);
    return () => window.clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    const root = chatLog();
    if (!root || !query) return;
    let timer = 0;
    const observer = new MutationObserver(() => {
      window.clearTimeout(timer);
      timer = window.setTimeout(search, 200);
    });
    observer.observe(root, { childList: true, subtree: true, characterData: true });
    return () => {
      observer.disconnect();
      window.clearTimeout(timer);
    };
  }, [query, search]);

  const current = ranges.length > 0 ? Math.min(index, ranges.length - 1) : -1;

  useEffect(() => {
    clearHighlights();
    if (ranges.length === 0) return;
    CSS.highlights.set(ALL, new Highlight(...ranges));
    CSS.highlights.set(CURRENT, new Highlight(ranges[current]));
  }, [ranges, current]);

  useEffect(() => clearHighlights, []);

  useEffect(() => {
    if (current < 0) return;
    ranges[current].startContainer.parentElement?.scrollIntoView({ block: "center" });
  }, [ranges, current]);

  const step = (by: 1 | -1) => {
    if (ranges.length === 0) return;
    setIndex((current + by + ranges.length) % ranges.length);
  };
  const stepRef = useRef(step);
  stepRef.current = step;
  useEffect(() => onFindStep((by) => stepRef.current(by)), []);

  const iconButton =
    "flex h-7 w-7 items-center justify-center rounded-md text-[var(--muted)] hover:bg-[var(--card-bg)] hover:text-[var(--fg)] disabled:opacity-40 disabled:hover:bg-transparent";
  return (
    <div
      role="search"
      className="absolute right-4 top-12 z-30 flex h-11 items-center gap-1 rounded-xl border border-[var(--border)] bg-[var(--panel-bg)] pl-3 pr-1.5 shadow-[var(--shadow)]"
    >
      <input
        ref={inputRef}
        aria-label="Find in page"
        className="w-52 bg-transparent text-sm outline-none placeholder:text-[var(--muted)]"
        placeholder="Find in page"
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setIndex(0);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            step(e.shiftKey ? -1 : 1);
          } else if (e.key === "Escape") {
            e.preventDefault();
            onClose();
          }
        }}
      />
      <span className="min-w-12 text-right text-xs tabular-nums text-[var(--muted)]" aria-live="polite">
        {query ? `${current + 1}/${ranges.length}` : ""}
      </span>
      <button type="button" aria-label="Previous match" className={iconButton} disabled={ranges.length === 0} onClick={() => step(-1)}>
        <ArrowUpIcon className="h-4 w-4" />
      </button>
      <button type="button" aria-label="Next match" className={iconButton} disabled={ranges.length === 0} onClick={() => step(1)}>
        <ArrowDownIcon className="h-4 w-4" />
      </button>
      <button type="button" aria-label="Close find" className={iconButton} onClick={onClose}>
        <CloseIcon className="h-4 w-4" />
      </button>
    </div>
  );
}
