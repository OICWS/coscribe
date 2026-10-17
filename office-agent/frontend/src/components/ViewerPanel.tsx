import { isPptx } from "../lib/slides";
import { DocxViewerPanel } from "./DocxViewerPanel";
import { SlidesPanel } from "./slides/SlidesPanel";

/** The viewer panel's content: a deck in the Slides panel, a Word file in the Word viewer. */
export function ViewerPanel({ threadId, path, refreshKey }: { threadId: string; path: string; refreshKey: string }) {
  return isPptx(path) ? (
    <SlidesPanel threadId={threadId} path={path} refreshKey={refreshKey} />
  ) : (
    <DocxViewerPanel threadId={threadId} path={path} refreshKey={refreshKey} />
  );
}
