import { useEffect, useRef, useState } from "react";

const MIN_PANEL_WIDTH = 320;
// Sanity ceiling only -- the real cap is the window's own width (see the
// drag handler below), so this just guards against something absurd.
const MAX_PANEL_WIDTH_ABSOLUTE = 1600;
// How far short of the window's full width dragging is allowed to go --
// enough to keep the nav rail and some chat content visible rather than
// literally filling the window edge to edge.
const PANEL_WIDTH_WINDOW_MARGIN = 300;
const DEFAULT_PANEL_WIDTH = 440;

/** The panel's width, dragged from its left edge; "expanded" widens it to
 * all but a strip of the window. */
export function usePanelWidth() {
  const [width, setWidth] = useState(DEFAULT_PANEL_WIDTH);
  const [expanded, setExpanded] = useState(false);
  const draggingRef = useRef(false);
  const maxWidth = () =>
    Math.min(MAX_PANEL_WIDTH_ABSOLUTE, Math.max(MIN_PANEL_WIDTH, window.innerWidth - PANEL_WIDTH_WINDOW_MARGIN));

  useEffect(() => {
    const onMouseMove = (e: MouseEvent) => {
      if (!draggingRef.current) return;
      setExpanded(false);
      setWidth(Math.min(maxWidth(), Math.max(MIN_PANEL_WIDTH, Math.round(window.innerWidth - e.clientX))));
    };
    const onMouseUp = () => {
      draggingRef.current = false;
      document.body.style.removeProperty("cursor");
      document.body.style.removeProperty("user-select");
    };
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };
  }, []);

  const onResizeHandleMouseDown = (e: React.MouseEvent) => {
    e.preventDefault();
    draggingRef.current = true;
    // Otherwise the drag selects the text it passes over.
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  };

  return {
    width: expanded ? maxWidth() : width,
    expanded,
    toggleExpanded: () => setExpanded((v) => !v),
    onResizeHandleMouseDown,
  };
}
