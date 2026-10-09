import { DRAWS_TITLE_BAR, TITLE_BAR_HEIGHT_PX, useBackdropLayers } from "../lib/titleBar";

/** The title bar's dimming under an open dialog. A dialog's backdrop starts
 * below the title bar (index.css), so that the menu, the sidebar button and
 * the drag area keep working like the OS window buttons do; this lays the
 * same dimming over them, without taking their clicks. */
export function TitleBarDim() {
  const layers = useBackdropLayers();
  if (!DRAWS_TITLE_BAR) return null;
  return (
    <>
      {layers.map(({ rgb, alpha }, i) => (
        <div
          key={i}
          aria-hidden
          className="pointer-events-none fixed inset-x-0 top-0 z-[70]"
          style={{ height: TITLE_BAR_HEIGHT_PX, background: `rgb(${rgb.join(" ")} / ${alpha})` }}
        />
      ))}
    </>
  );
}
