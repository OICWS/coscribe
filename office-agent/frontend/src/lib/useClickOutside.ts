import { useEffect, type RefObject } from "react";

/** Mirrors app.js's single global "click anywhere else closes the open
 * popup" listener -- each popup here owns its own instance instead of a
 * shared document-level list, but the effect is the same. */
export function useClickOutside(ref: RefObject<HTMLElement | null>, onOutside: () => void, active: boolean) {
  useEffect(() => {
    if (!active) return;
    const handler = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) onOutside();
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [active, onOutside, ref]);
}
