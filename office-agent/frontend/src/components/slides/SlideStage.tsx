import { forwardRef, type ReactNode } from "react";
import { SLIDE_HEIGHT, SLIDE_WIDTH, type SlideInfo } from "../../lib/slides";
import { SlideThumb, type SlidesView } from "./SlideStrip";

/** Room the floating toolbar takes above the slide, and the margin around it. */
export const TOOLBAR_ROOM = 76;
export const MARGIN = 32;
export const CANVAS_GAP = 24;

/** The grey area the slide sits on: one slide fitted, or every slide in Canvas view. The
 * toolbar and the mini bar float over it and stay put while it scrolls. */
export const SlideStage = forwardRef<
  HTMLDivElement,
  {
    view: SlidesView;
    slides: SlideInfo[];
    current: number;
    zoom: number;
    srcOf: (index: number) => string;
    onSelect: (index: number) => void;
    onOpen: (index: number) => void;
    children: ReactNode;
  }
>(function SlideStage({ view, slides, current, zoom, srcOf, onSelect, onOpen, children }, ref) {
  const slide = slides[current];
  const padding = `${TOOLBAR_ROOM}px ${MARGIN}px ${MARGIN}px`;
  return (
    <div ref={ref} className="relative min-h-0 flex-1 bg-[var(--stage-bg)]">
      <div className="absolute inset-0 overflow-auto">
        {view === "slide" ? (
          <div className="flex min-h-full min-w-full" style={{ padding }}>
            {slide && (
              <div
                className="m-auto shrink-0 overflow-hidden shadow-[0_2px_14px_rgba(0,0,0,0.12)]"
                style={{
                  width: SLIDE_WIDTH * zoom,
                  height: SLIDE_HEIGHT * zoom,
                  background: slide.background ?? "#ffffff",
                }}
              >
                <SlideThumb slide={slide} src={srcOf(current)} />
              </div>
            )}
          </div>
        ) : (
          <CanvasGrid slides={slides} current={current} zoom={zoom} padding={padding} srcOf={srcOf} onSelect={onSelect} onOpen={onOpen} />
        )}
      </div>
      {children}
    </div>
  );
});

/** Every slide at once; a click picks one, a double-click opens it in Slide view. */
function CanvasGrid({
  slides,
  current,
  zoom,
  padding,
  srcOf,
  onSelect,
  onOpen,
}: {
  slides: SlideInfo[];
  current: number;
  zoom: number;
  padding: string;
  srcOf: (index: number) => string;
  onSelect: (index: number) => void;
  onOpen: (index: number) => void;
}) {
  return (
    <div className="flex flex-wrap content-start" style={{ padding, gap: CANVAS_GAP }}>
      {slides.map((s, i) => (
        <button
          key={s.name}
          type="button"
          aria-label={`Slide ${i + 1}`}
          aria-current={i === current}
          className="text-left"
          onClick={() => onSelect(i)}
          onDoubleClick={() => onOpen(i)}
        >
          <span className={`block pb-1 text-sm ${i === current ? "text-[var(--accent)]" : "text-[var(--muted)]"}`}>{i + 1}</span>
          <span
            className={`block overflow-hidden rounded-md border-2 bg-white shadow-sm ${i === current ? "border-[var(--accent)]" : "border-transparent"}`}
            style={{ width: SLIDE_WIDTH * zoom, height: SLIDE_HEIGHT * zoom }}
          >
            <SlideThumb slide={s} src={srcOf(i)} />
          </span>
        </button>
      ))}
    </div>
  );
}
