import { useEffect, useRef, type ReactNode } from "react";
import type { SlideInfo } from "../../lib/slides";
import { BookOpenIcon, ChevronDownIcon, ChevronRightIcon } from "../icons";
import { CanvasViewIcon, ChevronLeftIcon, SlideViewIcon, ThumbnailsIcon } from "./slideIcons";

export type SlidesView = "slide" | "canvas";

const barButton =
  "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-[var(--fg)] hover:bg-[var(--card-bg)] disabled:opacity-40 disabled:hover:bg-transparent";
const barButtonOn =
  "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[var(--accent-soft)] text-[var(--accent-ink)] ring-1 ring-[var(--accent)]";
const segment = (on: boolean) =>
  `flex h-7 w-8 items-center justify-center rounded-md ${on ? "bg-[var(--bg)] text-[var(--fg)] shadow-sm" : "text-[var(--muted)] hover:text-[var(--fg)]"}`;

/** A slide drawn small: its picture, or for an edited slide its background until the editor draws it. */
export function SlideThumb({ slide, src }: { slide: SlideInfo; src: string }) {
  return slide.kind === "page" ? (
    <img src={src} alt="" draggable={false} className="block h-full w-full object-contain" />
  ) : (
    <div className="h-full w-full" style={{ background: slide.background ?? "#ffffff" }} />
  );
}

/** Notes, the Slide and Canvas views, and hiding the strip, above the thumbnails (`children`). */
export function SlideFooter({
  view,
  notesShown,
  onView,
  onToggleNotes,
  onHideStrip,
  children,
}: {
  view: SlidesView;
  notesShown: boolean;
  onView: (view: SlidesView) => void;
  onToggleNotes: () => void;
  onHideStrip: () => void;
  children: ReactNode;
}) {
  return (
    <div className="shrink-0 border-t border-[var(--border)] bg-[var(--bg)]">
      <div className="flex h-11 shrink-0 items-center gap-1 px-3">
        <button
          type="button"
          title={notesShown ? "Hide speaker notes" : "Speaker notes"}
          aria-label="Speaker notes"
          aria-pressed={notesShown}
          className={notesShown ? barButtonOn : barButton}
          onClick={onToggleNotes}
        >
          <BookOpenIcon className="h-[17px] w-[17px]" />
        </button>
        <div className="flex-1" />
        <div role="radiogroup" aria-label="View" className="flex rounded-lg bg-[var(--card-bg)] p-0.5">
          <button
            type="button"
            role="radio"
            aria-checked={view === "slide"}
            title="Slide"
            aria-label="Slide"
            className={segment(view === "slide")}
            onClick={() => onView("slide")}
          >
            <SlideViewIcon className="h-4 w-4" />
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={view === "canvas"}
            title="Canvas"
            aria-label="Canvas"
            className={segment(view === "canvas")}
            onClick={() => onView("canvas")}
          >
            <CanvasViewIcon className="h-4 w-4" />
          </button>
        </div>
        <button
          type="button"
          className={barButton}
          disabled={view === "canvas"}
          title={view === "canvas" ? "Canvas view is showing, so there is no thumbnail strip to hide" : "Hide slide thumbnails"}
          aria-label="Hide slide thumbnails"
          onClick={onHideStrip}
        >
          <ChevronDownIcon className="h-4 w-4" />
        </button>
      </div>
      {children}
    </div>
  );
}

/** The slides in a row; the current one outlined, and kept in sight as it changes. */
export function SlideThumbnails({
  slides,
  current,
  srcOf,
  onSelect,
}: {
  slides: SlideInfo[];
  current: number;
  srcOf: (index: number) => string;
  onSelect: (index: number) => void;
}) {
  const rowRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    rowRef.current?.children[current]?.scrollIntoView({
      block: "nearest",
      inline: "nearest",
    });
  }, [current]);
  return (
    <div ref={rowRef} className="flex shrink-0 gap-2.5 overflow-x-auto px-3 pb-3 pt-0.5">
      {slides.map((slide, i) => (
        <button
          key={slide.name}
          type="button"
          aria-label={`Slide ${i + 1}`}
          aria-current={i === current}
          className={`relative aspect-video w-[156px] shrink-0 overflow-hidden rounded-lg border-2 bg-white ${i === current ? "border-[var(--accent)]" : "border-transparent hover:border-[var(--border-hover)]"}`}
          onClick={() => onSelect(i)}
        >
          <SlideThumb slide={slide} src={srcOf(i)} />
          <span className="absolute bottom-1.5 left-1.5 flex h-5 min-w-5 items-center justify-center rounded-md border border-[var(--border)] bg-[var(--bg)] px-1 text-[11px] text-[var(--fg)]">
            {i + 1}
          </span>
        </button>
      ))}
    </div>
  );
}

/** With the strip hidden: previous, the page, next, notes, and the strip back. */
export function SlideMiniBar({
  current,
  count,
  notesShown,
  onGo,
  onToggleNotes,
  onShowStrip,
}: {
  current: number;
  count: number;
  notesShown: boolean;
  onGo: (delta: number) => void;
  onToggleNotes: () => void;
  onShowStrip: () => void;
}) {
  const small =
    "flex h-7 w-7 items-center justify-center rounded-md hover:bg-[var(--card-bg)] disabled:opacity-40 disabled:hover:bg-transparent";
  return (
    <div className="absolute bottom-4 left-1/2 z-10 flex -translate-x-1/2 items-center gap-0.5 rounded-full border border-[var(--border)] bg-[var(--bg)] px-2 py-1 text-[var(--fg)] shadow-[var(--panel-shadow)]">
      <button
        type="button"
        className={small}
        title="Previous slide"
        aria-label="Previous slide"
        disabled={current === 0}
        onClick={() => onGo(-1)}
      >
        <ChevronLeftIcon className="h-4 w-4" />
      </button>
      <span className="px-1.5 text-sm tabular-nums">
        {current + 1} <span className="text-[var(--muted)]">/ {count}</span>
      </span>
      <button
        type="button"
        className={small}
        title="Next slide"
        aria-label="Next slide"
        disabled={current >= count - 1}
        onClick={() => onGo(1)}
      >
        <ChevronRightIcon className="h-4 w-4" />
      </button>
      <span className="mx-1 h-4 border-l border-[var(--border)]" />
      <button
        type="button"
        className={`${small} ${notesShown ? "bg-[var(--accent-soft)] text-[var(--accent-ink)]" : ""}`}
        title="Speaker notes"
        aria-label="Speaker notes"
        aria-pressed={notesShown}
        onClick={onToggleNotes}
      >
        <BookOpenIcon className="h-4 w-4" />
      </button>
      <button type="button" className={small} title="Show slide thumbnails" aria-label="Show slide thumbnails" onClick={onShowStrip}>
        <ThumbnailsIcon className="h-4 w-4" />
      </button>
    </div>
  );
}
