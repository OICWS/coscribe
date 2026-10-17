import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { openThreadFile } from "../../lib/rest";
import { DeckNotBuilt, ZOOM_STEPS, fitZoom, getSlideDeck, slideImageUrl, stepZoom, zoomLabel, type SlideDeck } from "../../lib/slides";
import { useDockPanel } from "../dock/dockContext";
import { ChevronDownIcon, ExternalLinkIcon, FolderIcon } from "../icons";
import { SlideMenu, type MenuEntry } from "./SlideMenu";
import { SlideProperties } from "./SlideProperties";
import { CANVAS_GAP, MARGIN, SlideStage, TOOLBAR_ROOM } from "./SlideStage";
import { SlideToolbar } from "./SlideToolbar";
import { SlideFooter, SlideMiniBar, SlideThumbnails, type SlidesView } from "./SlideStrip";

/** From this panel width the properties sit beside the slide instead of below it. */
const PROPERTIES_BESIDE_FROM = 880;

type Menu = { kind: "zoom" | "title"; anchor: DOMRect };

/** A deck coscribe built, beside the conversation: the slide, its thumbnails, its notes and
 * properties, and all slides at once in Canvas view. Read only for now. */
export function SlidesPanel({ threadId, path, refreshKey }: { threadId: string; path: string; refreshKey: string }) {
  const dock = useDockPanel();
  const { deck, error, notBuilt, setError } = useSlideDeck(threadId, path, refreshKey);
  const [menu, setMenu] = useState<Menu | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const { stage, panelWidth } = useSizes(rootRef, stageRef, deck !== null);

  const slides = deck?.slides ?? [];
  const ui = useSlidesUi(slides.length);
  const { index, view, setCurrent, showView, toggleNotes } = ui;
  const shownZoom = ui.zoom ?? fitFor(view, stage);
  const srcOf = useCallback((i: number) => slideImageUrl(threadId, path, i, refreshKey), [threadId, path, refreshKey]);
  const open = (reveal: boolean) => void openThreadFile(threadId, path, reveal).then((e) => e && setError(e));
  const name = path.split(/[\\/]/).pop() ?? path;
  const slide = slides[index];
  const beside = panelWidth >= PROPERTIES_BESIDE_FROM;
  const properties = ui.propertiesShown && slide && (
    <SlideProperties slide={slide} beside={beside} onClose={() => ui.setPropertiesShown(false)} />
  );

  return (
    <div ref={rootRef} tabIndex={-1} className="flex h-full min-h-0 flex-col outline-none" onKeyDown={(e) => ui.onKey(e, shownZoom)}>
      <div className="flex h-12 shrink-0 items-center gap-2 px-3" {...dock.headerProps}>
        <TitleButton title={deck?.title ?? name} path={path} onMenu={(anchor) => setMenu({ kind: "title", anchor })} />
        <div className="flex-1" />
        {dock.controls}
      </div>
      <DeckNotices changed={deck?.changed ?? false} error={error} />
      {notBuilt ? (
        <NotBuilt name={name} onOpen={() => open(false)} />
      ) : (
        <div className="flex min-h-0 flex-1">
          <div className="flex min-w-0 flex-1 flex-col">
            <SlideStage
              ref={stageRef}
              view={view}
              slides={slides}
              current={index}
              zoom={shownZoom}
              srcOf={srcOf}
              onSelect={setCurrent}
              onOpen={ui.openSlide}
            >
              <SlideToolbar
                zoom={shownZoom}
                propertiesShown={ui.propertiesShown}
                beside={beside}
                onToggleProperties={() => ui.setPropertiesShown((shown) => !shown)}
                onZoomMenu={(anchor) => setMenu({ kind: "zoom", anchor })}
              />
              {view === "slide" && !ui.stripShown && slides.length > 0 && (
                <SlideMiniBar
                  current={index}
                  count={slides.length}
                  notesShown={ui.notesShown}
                  onGo={ui.go}
                  onToggleNotes={toggleNotes}
                  onShowStrip={() => ui.setStripShown(true)}
                />
              )}
            </SlideStage>
            {ui.notesShown && view === "slide" && slide && <SpeakerNotes notes={slide.notes} />}
            {(view === "canvas" || ui.stripShown) && (
              <SlideFooter
                view={view}
                notesShown={ui.notesShown}
                onView={showView}
                onToggleNotes={toggleNotes}
                onHideStrip={() => ui.setStripShown(false)}
              >
                {view === "slide" && <SlideThumbnails slides={slides} current={index} srcOf={srcOf} onSelect={setCurrent} />}
              </SlideFooter>
            )}
            {!beside && properties}
          </div>
          {beside && properties}
        </div>
      )}
      {menu && (
        <SlideMenu
          anchor={menu.anchor}
          label={menu.kind === "zoom" ? "Zoom" : "Presentation"}
          width={menu.kind === "zoom" ? 210 : 220}
          entries={menu.kind === "zoom" ? zoomEntries(shownZoom, view, ui.setZoom) : titleEntries(open)}
          onClose={() => setMenu(null)}
        />
      )}
    </div>
  );
}

function fitFor(view: SlidesView, stage: { width: number; height: number }): number {
  if (view === "slide") return fitZoom(stage.width - 2 * MARGIN, stage.height - TOOLBAR_ROOM - MARGIN);
  const columns = stage.width < 560 ? 2 : 3;
  return fitZoom((stage.width - 2 * MARGIN - (columns - 1) * CANVAS_GAP) / columns, Infinity);
}

function DeckNotices({ changed, error }: { changed: boolean; error: string | null }) {
  return (
    <>
      {changed && (
        <div className="shrink-0 border-t border-[var(--border)] px-4 py-1.5 text-xs text-[var(--warning)]">
          Shown as coscribe last built it; the file has changed since.
        </div>
      )}
      {error && <div className="shrink-0 px-4 py-2 text-xs text-[var(--danger)]">{error}</div>}
    </>
  );
}

function TitleButton({ title, path, onMenu }: { title: string; path: string; onMenu: (anchor: DOMRect) => void }) {
  return (
    <button
      type="button"
      className="flex min-w-0 items-center gap-1 rounded-md px-1 py-0.5 text-[15px] hover:bg-[var(--card-bg)]"
      title={path}
      aria-haspopup="menu"
      onPointerDown={(e) => e.stopPropagation()}
      onClick={(e) => onMenu(e.currentTarget.getBoundingClientRect())}
    >
      <span className="truncate">{title}</span>
      <ChevronDownIcon className="h-3.5 w-3.5 shrink-0 text-[var(--muted)]" />
    </button>
  );
}

/** The slide's speaker notes under it; written by the editor later, read here. */
function SpeakerNotes({ notes }: { notes: string }) {
  return (
    <div
      aria-label="Speaker notes"
      className="h-[120px] shrink-0 overflow-auto whitespace-pre-wrap border-t border-[var(--border)] bg-[var(--bg)] px-4 py-3 text-[15px] text-[var(--fg)]"
    >
      {notes || <span className="text-[var(--muted)]">No speaker notes on this slide.</span>}
    </div>
  );
}

/** Which slide, which view, and what is shown around it; the keys that change them. */
function useSlidesUi(count: number) {
  const [current, setCurrent] = useState(0);
  const [view, setView] = useState<SlidesView>("slide");
  const [stripShown, setStripShown] = useState(true);
  const [notesShown, setNotesShown] = useState(false);
  const [propertiesShown, setPropertiesShown] = useState(false);
  const [zoom, setZoom] = useState<number | null>(null);
  const index = Math.min(current, Math.max(count - 1, 0));
  const go = (delta: number) => setCurrent(Math.max(0, Math.min(count - 1, index + delta)));
  const showView = (next: SlidesView) => {
    setView(next);
    setZoom(null);
  };
  const openSlide = (i: number) => {
    setCurrent(i);
    showView("slide");
  };
  const onKey = (e: KeyboardEvent<HTMLDivElement>, shownZoom: number) => {
    const key = slideKey(e, view);
    if (key === null) return;
    e.preventDefault();
    if (typeof key === "number") go(key);
    else if (key === "fit") setZoom(null);
    else if (key === "in" || key === "out") setZoom(stepZoom(shownZoom, key === "in" ? 1 : -1));
    else if (key === "open") openSlide(index);
    else setCurrent(key === "first" ? 0 : count - 1);
  };
  const toggleNotes = () => setNotesShown((shown) => !shown);
  return {
    index,
    view,
    zoom,
    stripShown,
    notesShown,
    propertiesShown,
    setCurrent,
    setZoom,
    setStripShown,
    setPropertiesShown,
    go,
    showView,
    openSlide,
    onKey,
    toggleNotes,
  };
}

function useSlideDeck(threadId: string, path: string, refreshKey: string) {
  const [deck, setDeck] = useState<SlideDeck | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notBuilt, setNotBuilt] = useState(false);
  useEffect(() => {
    let live = true;
    getSlideDeck(threadId, path).then(
      (loaded) => {
        if (!live) return;
        setDeck(loaded);
        setError(null);
      },
      (e: unknown) => {
        if (!live) return;
        if (e instanceof DeckNotBuilt) setNotBuilt(true);
        else setError(e instanceof Error ? e.message : String(e));
      },
    );
    return () => {
      live = false;
    };
  }, [threadId, path, refreshKey]);
  return { deck, error, notBuilt, setError };
}

function useSizes(rootRef: React.RefObject<HTMLDivElement | null>, stageRef: React.RefObject<HTMLDivElement | null>, ready: boolean) {
  const [stage, setStage] = useState({ width: 0, height: 0 });
  const [panelWidth, setPanelWidth] = useState(0);
  useEffect(() => {
    const observer = new ResizeObserver(() => {
      const box = stageRef.current?.getBoundingClientRect();
      if (box) setStage({ width: box.width, height: box.height });
      setPanelWidth(rootRef.current?.getBoundingClientRect().width ?? 0);
    });
    if (stageRef.current) observer.observe(stageRef.current);
    if (rootRef.current) observer.observe(rootRef.current);
    return () => observer.disconnect();
  }, [rootRef, stageRef, ready]);
  return { stage, panelWidth };
}

type SlideKey = "in" | "out" | "fit" | "first" | "last" | "open" | 1 | -1;

/** What a key does in the panel; nothing while typing in a field. */
function slideKey(e: KeyboardEvent<HTMLDivElement>, view: SlidesView): SlideKey | null {
  if ((e.target as HTMLElement).closest("input, textarea, [contenteditable]")) return null;
  if (e.ctrlKey || e.metaKey) {
    return ({ "=": "in", "+": "in", "-": "out", "0": "fit" } as const)[e.key as "=" | "+" | "-" | "0"] ?? null;
  }
  if (["ArrowRight", "ArrowDown", "PageDown"].includes(e.key)) return 1;
  if (["ArrowLeft", "ArrowUp", "PageUp"].includes(e.key)) return -1;
  if (e.key === "Home") return "first";
  if (e.key === "End") return "last";
  if (e.key === "Enter" && view === "canvas") return "open";
  return null;
}

function zoomEntries(zoom: number, view: SlidesView, setZoom: (zoom: number | null) => void): MenuEntry[] {
  const now = (step: number) => Math.abs(step - zoom) < 1e-6;
  return [
    {
      label: "Zoom in",
      hint: "Ctrl +",
      disabled: stepZoom(zoom, 1) === zoom,
      onSelect: () => setZoom(stepZoom(zoom, 1)),
    },
    {
      label: "Zoom out",
      hint: "Ctrl −",
      disabled: stepZoom(zoom, -1) === zoom,
      onSelect: () => setZoom(stepZoom(zoom, -1)),
    },
    {
      label: view === "slide" ? "Fit the slide" : "Fit the slides",
      onSelect: () => setZoom(null),
    },
    "divider",
    { note: `Now ${zoomLabel(zoom)}` },
    ...ZOOM_STEPS.map((step) => ({
      label: zoomLabel(step),
      checked: now(step),
      disabled: now(step),
      onSelect: () => setZoom(step),
    })),
    "divider",
    { label: "Undo", hint: "Ctrl+Z", disabled: true },
    { label: "Redo", hint: "Ctrl+Shift+Z", disabled: true },
  ];
}

function titleEntries(open: (reveal: boolean) => void): MenuEntry[] {
  return [
    {
      label: "Open in PowerPoint",
      icon: <ExternalLinkIcon className="h-4 w-4" />,
      onSelect: () => open(false),
    },
    {
      label: "Show in folder",
      icon: <FolderIcon className="h-4 w-4" />,
      onSelect: () => open(true),
    },
  ];
}

function NotBuilt({ name, onOpen }: { name: string; onOpen: () => void }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 bg-[var(--stage-bg)] p-6 text-center">
      <p className="max-w-[360px] text-sm text-[var(--muted)]">
        {name} was not made by coscribe from slide pages, so it can't be shown here yet.
      </p>
      <button
        type="button"
        className="flex h-9 items-center gap-2 rounded-lg border border-[var(--border)] bg-[var(--bg)] px-3 text-sm hover:bg-[var(--card-bg)]"
        onClick={onOpen}
      >
        <ExternalLinkIcon className="h-4 w-4" />
        Open in PowerPoint
      </button>
    </div>
  );
}
