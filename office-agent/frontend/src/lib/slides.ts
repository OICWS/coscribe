/** A deck built from pages, as the server reads it (`GET /api/threads/{id}/slides`). */
export interface SlideInfo {
  name: string;
  /** "page": an unedited SVG page, drawn as an image. "section": an edited slide. */
  kind: "page" | "section";
  notes: string;
  /** PowerPoint's name for the transition ("fade", "push", "morph", ...), or null. */
  transition: string | null;
  /** "#RRGGBB", or null when the slide has no background of its own. */
  background: string | null;
}

export interface SlideDeck {
  title: string;
  folder: string;
  /** The .pptx or a page changed since the last build: what is shown may differ from the file. */
  changed: boolean;
  slides: SlideInfo[];
}

/** The slide's own size in the panel's units: 100% zoom draws it 1920 pixels wide. */
export const SLIDE_WIDTH = 1920;
export const SLIDE_HEIGHT = 1080;
export const ZOOM_STEPS = [0.25, 0.5, 0.75, 1, 1.5, 2];
const MIN_ZOOM = 0.05;
const MAX_ZOOM = 2;

export const isPptx = (path: string) => /\.pptx$/i.test(path);

export class DeckNotBuilt extends Error {}

const base = (threadId: string) => `/api/threads/${encodeURIComponent(threadId)}/slides`;

export async function getSlideDeck(threadId: string, path: string): Promise<SlideDeck> {
  const response = await fetch(`${base(threadId)}?path=${encodeURIComponent(path)}`);
  if (response.status === 409) throw new DeckNotBuilt();
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as {
      error?: string;
    };
    throw new Error(body.error ?? `HTTP ${response.status}`);
  }
  return (await response.json()) as SlideDeck;
}

/** The slide as one SVG with nothing outside it; `version` changes when the deck is rebuilt. */
export function slideImageUrl(threadId: string, path: string, index: number, version: string): string {
  return `${base(threadId)}/${index}/svg?path=${encodeURIComponent(path)}&v=${encodeURIComponent(version)}`;
}

/** The zoom that fits a slide inside `width` × `height`. */
export function fitZoom(width: number, height: number): number {
  const zoom = Math.min(width / SLIDE_WIDTH, height / SLIDE_HEIGHT);
  return Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, zoom));
}

/** The next preset zoom past `zoom` in `direction`, or `zoom` when there is none. */
export function stepZoom(zoom: number, direction: 1 | -1): number {
  const next = direction > 0 ? ZOOM_STEPS.find((step) => step > zoom + 1e-6) : [...ZOOM_STEPS].reverse().find((step) => step < zoom - 1e-6);
  return next ?? zoom;
}

export const zoomLabel = (zoom: number) => `${Math.round(zoom * 100)}%`;

/** PowerPoint's transition names as the properties panel says them. */
export function transitionLabel(name: string | null): string {
  if (!name) return "None";
  if (name === "morph") return "Magic move";
  return name.charAt(0).toUpperCase() + name.slice(1);
}
