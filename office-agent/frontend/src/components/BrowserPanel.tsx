import { useEffect, useRef, useState } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";
import type { UnlistenFn } from "@tauri-apps/api/event";
import { CloseIcon } from "./icons";
import { browserPanelClose, browserPanelOpen, browserPanelReposition, isTauri, type PanelRect } from "../lib/tauri";
import {
  browserPanelBack as electronBrowserPanelBack,
  browserPanelClose as electronBrowserPanelClose,
  browserPanelForward as electronBrowserPanelForward,
  browserPanelNavigate as electronBrowserPanelNavigate,
  browserPanelOpen as electronBrowserPanelOpen,
  browserPanelReload as electronBrowserPanelReload,
  browserPanelReposition as electronBrowserPanelReposition,
  browserPanelSetPickMode as electronBrowserPanelSetPickMode,
  isElectron,
  onBrowserPanelLoadError,
  onBrowserPanelNavigated,
  onBrowserPanelPicked,
  type BrowserPanelRect,
} from "../lib/electron";

/** Same shape as Composer.tsx's own PendingImage on purpose -- lets
 * App.tsx hand a captured element straight to Composer's existing
 * pendingImages list with no translation step. `text`/`tag` are the
 * picked element's own innerText/tag name (already extracted server-side
 * by pick_element, previously captured here and shown in the preview
 * but then dropped entirely at "Add to chat" -- the model only ever saw
 * a picture, never the actual text, even though the backend had already
 * read it) -- optional since a plain file/paste-uploaded image has
 * neither. */
export interface BrowserCapture {
  name: string;
  dataUrl: string;
  text?: string;
  tag?: string;
}

interface PickedElement {
  screenshot: string; // base64 jpeg, no data: prefix yet
  text: string;
  tag: string;
}

interface ElementRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

interface HoveredElement {
  tag: string;
  rect: ElementRect;
}

type PanelStatus = "connecting" | "ready" | "error";

const MIN_PANEL_WIDTH = 320;
// Sanity ceiling only -- the real cap is the window's own width (see the
// drag handler below), so this just guards against something absurd.
const MAX_PANEL_WIDTH_ABSOLUTE = 1600;
// How far short of the window's full width dragging is allowed to go --
// enough to keep the nav rail and some chat content visible rather than
// literally filling the window edge to edge.
const PANEL_WIDTH_WINDOW_MARGIN = 300;
const DEFAULT_PANEL_WIDTH = 440;

/** The remote page's own emulated viewport size -- deliberately the
 * user's real screen resolution, not this panel's own (often much
 * narrower, e.g. the 440px default) on-screen display size. Real-
 * reported: rendering the remote page at the panel's own narrow width
 * made most real sites trigger their own responsive "narrow/mobile"
 * layout (hamburger menus, single-column, larger relative text) instead
 * of the normal desktop layout the user sees in their own browser --
 * not a sharpness/DPI problem (already separately handled via
 * devicePixelRatio), a *layout* one. Same pattern any remote-desktop
 * viewer (VNC, RDP, Chrome Remote Desktop) uses: render at the real
 * target resolution, display a scaled-down view of it, let the viewer
 * resize their own window wider to see more detail -- this panel
 * already supports dragging up to MAX_PANEL_WIDTH_ABSOLUTE for exactly
 * that. `window.screen.width`/`height` are already in CSS pixels per
 * spec (not multiplied by devicePixelRatio), matching what
 * Emulation.setDeviceMetricsOverride's own width/height expect.
 *
 * Read fresh at each call site rather than cached in state: it's a
 * plain global, not React state, so there's no staleness/closure
 * concern the way canvasSize's own ref-mirroring exists to solve, and
 * it practically never changes mid-session (only a real monitor
 * change would do it, not worth a resize listener for).
 *
 * Real tradeoff, not hidden: screencast frames are now sized to the
 * real screen resolution (e.g. 1920x1080, or more at a high
 * devicePixelRatio), not the panel's own narrow display -- genuinely
 * more data per JPEG frame than before. Not verified against real
 * Windows hardware whether this reintroduces the performance
 * complaints the panel's own debounced-resize fix (Phase 8x) addressed
 * -- worth specifically watching for on the next real-hardware test. */
function remoteViewportSize(): { width: number; height: number } {
  return { width: window.screen.width, height: window.screen.height };
}

/** Live embedded browser for showing-not-just-describing a real page to
 * the model -- point at something instead of writing a paragraph about
 * it. A real headless Chromium the backend drives over the Chrome
 * DevTools Protocol (web/browser_panel.py), screencasting frames to this
 * canvas and forwarding this panel's own mouse/keyboard back to it -- not
 * an iframe (most real sites this would actually matter for block
 * framing outright) and not the model's own separate playwright MCP
 * automation (that one the model drives itself, never shown live).
 *
 * Deliberately narrow for v1 -- see browser_panel.py's own docstring for
 * the full reasoning: no cookie import, no annotate/freehand-drawing
 * tool. What's here: navigate, back/forward/reload, click/type inside
 * the page, and "select an element" -- hover highlights whatever's under
 * the cursor live, with
 * a tag+size tooltip alongside it (the same preview-before-you-commit a
 * real DevTools element inspector gives you), a click commits it,
 * screenshots just that element, and hands it to onSendToChat once the
 * user confirms. The panel itself is drag-resizable from its own left
 * edge -- the remote page's own emulated viewport re-syncs to match
 * automatically (see the ResizeObserver effect below), the same path
 * that already keeps it in sync with the window being resized. */
export function BrowserPanel({
  onClose,
  onSendToChat,
}: {
  onClose: () => void;
  onSendToChat: (capture: BrowserCapture) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  // A real, invisible, focusable <input> (see the block near the bottom
  // of this component) that keyboard input actually targets instead of
  // the canvas -- the whole reason typing an IME language (Chinese/
  // Japanese/Korean) didn't work at all before this. A <canvas> is never
  // an editable surface by spec, so the OS input method never attaches a
  // composition to it no matter what keydown handling is added -- there
  // is no alternative to a real text-input element behind the scenes,
  // same trick noVNC/Guacamole use for exactly this problem. Composing
  // state (mid-IME-conversion) is tracked in composingRef so the
  // existing control-key forwarding below (Backspace/Enter/arrows/...)
  // can get entirely out of the IME's way while it's active.
  const imeInputRef = useRef<HTMLInputElement>(null);
  const composingRef = useRef(false);
  // Swallows exactly one keyup right after compositionend -- e.g. the
  // Enter/Space that just confirmed an IME candidate. Its keydown was
  // already correctly skipped (composingRef was still true then), but by
  // keyup composingRef has already flipped back to false (compositionend
  // fires before that keyup), so without this the keyup half alone would
  // still forward a stray, unmatched "keyUp" for that key to the remote
  // page. Not independently verified against a real IME on real
  // hardware -- browsers are known to be inconsistent about exactly when
  // isComposing flips for this specific key, so this is the defensive,
  // best-effort side to get wrong, not the common case.
  const justEndedCompositionRef = useRef(false);
  const [imePos, setImePos] = useState({ left: 0, top: 0 });
  const wsRef = useRef<WebSocket | null>(null);
  // Mirrors canvasSize below but readable synchronously from effects/
  // handlers that shouldn't themselves be re-run on every resize (a
  // plain state read there would either be stale -- captured at mount --
  // or force those effects to re-subscribe on every resize tick).
  const canvasSizeRef = useRef({ width: 960, height: 600 });
  const lastHoverSentRef = useRef(0);
  const draggingRef = useRef(false);
  // Mirrors `status` for the same reason canvasSizeRef mirrors
  // canvasSize -- read inside the WS "message" listener below, which is
  // registered once at mount and would otherwise only ever see status's
  // very first value ("connecting"), not its current one.
  const statusRef = useRef<PanelStatus>("connecting");
  const [status, setStatus] = useState<PanelStatus>("connecting");
  const [errorText, setErrorText] = useState<string | null>(null);
  // A BrowserPanelError arriving *after* the panel is already showing a
  // live view (pick_element finding nothing, back/forward run out of
  // history -- routine, expected outcomes, not connection failures) --
  // shown as a small dismissing banner instead of reusing errorText/
  // status="error", which replaces the whole live view with a full-panel
  // message and would otherwise hide a perfectly working browser over a
  // "you're already at the oldest page" notice.
  const [actionError, setActionError] = useState<string | null>(null);
  const [addressValue, setAddressValue] = useState("");
  const [pickMode, setPickMode] = useState(false);
  const [picked, setPicked] = useState<PickedElement | null>(null);
  const [canvasSize, setCanvasSize] = useState(canvasSizeRef.current);
  const [hoverElement, setHoverElement] = useState<HoveredElement | null>(null);
  const [panelWidth, setPanelWidth] = useState(DEFAULT_PANEL_WIDTH);
  // Stable for the life of this component (isTauri() can't change mid-
  // session) -- computed once here rather than calling isTauri() at
  // every use site below, purely for readability at the render/effect
  // call sites.
  const tauriMode = isTauri();
  // Surfaces a failed browser_panel_open/reposition instead of leaving
  // the placeholder silently saying "Native browser window" forever --
  // real-hardware-reported: the native window failing to appear at all
  // (a WebView2/permissions/positioning problem) looked visually
  // identical to it working but just not being visible yet, with no way
  // to tell the two apart without this. This is a release build, so
  // there is no devtools console to check either (same class of issue
  // as the now-deleted Stage 0 spike's eprintln! going nowhere).
  const [nativeError, setNativeError] = useState<string | null>(null);
  // Shown alongside the placeholder text -- the computed rect this side
  // actually sent to Rust, plus the CDP port browser_panel_open handed
  // back. A real, "open succeeded, no error thrown" real-hardware report
  // still showed the placeholder's own text uncovered (should be
  // impossible if the native window is genuinely positioned on top of
  // it and opaque), so the next thing worth ruling out is the rect
  // itself being wrong (off-screen, zero-sized, etc.) rather than
  // guessing blind again.
  const [nativeDebug, setNativeDebug] = useState<string | null>(null);

  // Electron migration Phase 2 -- see the effect below and electron.ts's
  // own module docs. electronMode is stable for the life of this
  // component, same reasoning tauriMode already documents.
  const electronMode = isElectron();
  const [electronUrl, setElectronUrl] = useState("");
  const [electronAddressValue, setElectronAddressValue] = useState("");
  const [electronCanGoBack, setElectronCanGoBack] = useState(false);
  const [electronCanGoForward, setElectronCanGoForward] = useState(false);
  const [electronLoadError, setElectronLoadError] = useState<string | null>(null);

  useEffect(() => {
    statusRef.current = status;
  }, [status]);

  useEffect(() => {
    if (!actionError) return;
    const timer = setTimeout(() => setActionError(null), 4000);
    return () => clearTimeout(timer);
  }, [actionError]);

  const send = (message: Record<string, unknown>) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) wsRef.current.send(JSON.stringify(message));
  };

  useEffect(() => {
    // Stage 1 of the Tauri native-window plan (see the effect below)
    // doesn't wire the panel window to any backend yet -- no screencast,
    // no CDP attach, nothing this WS connection would drive. Electron
    // mode has its own real navigation IPC (the effect further below) and
    // never needs this WS/CDP path at all. Guarding here rather than not
    // registering the effect at all keeps this file's Hooks call order
    // identical across all three paths (isTauri()/isElectron() never
    // change within one mount, so this is safe either way, but matching
    // React's own "same hooks every render" rule by convention rather
    // than relying on that is cheap insurance).
    if (tauriMode || electronMode) return;
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/browser`);
    wsRef.current = ws;

    ws.addEventListener("open", () => {
      setStatus("ready");
      // Tells the backend what to emulate -- the user's real screen
      // resolution (remoteViewportSize), not this panel's own on-screen
      // display size (see that function's own comment for why). Sent
      // once here on connect; unlike the panel's own display size, the
      // remote viewport no longer needs to be re-sent when the panel is
      // merely dragged wider/narrower -- see the ResizeObserver effect
      // below.
      const viewport = remoteViewportSize();
      send({
        type: "resize",
        width: viewport.width,
        height: viewport.height,
        scale: window.devicePixelRatio || 1,
      });
    });
    ws.addEventListener("close", () => setStatus((s) => (s === "error" ? s : "error")));
    ws.addEventListener("message", (ev) => {
      let msg: { type: string; [key: string]: unknown };
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (msg.type === "frame") {
        drawFrame(canvasRef.current, msg.data as string);
      } else if (msg.type === "picked") {
        setPicked({ screenshot: msg.screenshot as string, text: msg.text as string, tag: msg.tag as string });
        setPickMode(false);
      } else if (msg.type === "hover") {
        setHoverElement(msg.element as HoveredElement | null);
      } else if (msg.type === "error") {
        // Only a failure during the initial launch (still "connecting")
        // is fatal to the whole panel -- see actionError's own comment
        // above for why every other error stays a small banner instead.
        if (statusRef.current === "connecting") {
          setErrorText(msg.message as string);
          setStatus("error");
        } else {
          setActionError(msg.message as string);
        }
      }
    });

    return () => {
      ws.close();
      wsRef.current = null;
    };
    // tauriMode/electronMode are derived from isTauri()/isElectron(),
    // static environment checks that cannot change for the life of this
    // component -- included so this satisfies exhaustive-deps without
    // actually causing any re-subscription in practice.
  }, [tauriMode, electronMode]);

  // Browser panel native-window plan, Stage 1 (Tauri desktop only --
  // see ROADMAP.md "Browser panel: native second-window architecture").
  // Opens a real, separate, top-level WebviewWindow
  // (browser_panel_window.rs) and keeps it positioned exactly over this
  // panel's own placeholder region (the containerRef div rendered
  // below, in place of the canvas for this path) -- Rust itself
  // computes nothing, it only positions/sizes/shows/hides a window when
  // told to; every rect is computed here.
  //
  // innerPosition() (the webview *content* area's own screen origin),
  // not outerPosition() (the whole OS window including its title
  // bar/borders) -- a deliberate deviation from this plan's own
  // original design note, made once the real API was checked rather
  // than assumed: innerPosition() already excludes the title bar/border
  // thickness, which differs by OS theme and window state and isn't
  // otherwise computable from JS at all, so there's no separate chrome-
  // offset math to get wrong.
  //
  // Three independent signals can each change where this panel's own
  // placeholder sits on screen, so all three are watched: the main
  // window moving (onMoved), the main window resizing (onResized -- its
  // own content area can grow/shrink independently of the panel's own
  // width), and the placeholder div's own layout changing without the
  // window itself moving at all (the resize-handle drag above, or the
  // nav rail collapsing) -- a ResizeObserver on containerRef, same
  // signal the non-Tauri canvas path already uses for its own on-screen
  // size, reused here for repositioning instead.
  useEffect(() => {
    if (!tauriMode) return;
    let cancelled = false;
    let unlistenMoved: UnlistenFn | undefined;
    let unlistenResized: UnlistenFn | undefined;
    let resizeObserver: ResizeObserver | undefined;

    const computeRect = async (): Promise<PanelRect | null> => {
      const container = containerRef.current;
      if (!container) return null;
      const inner = await getCurrentWindow().innerPosition();
      const domRect = container.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      return {
        x: Math.round(inner.x + domRect.left * dpr),
        y: Math.round(inner.y + domRect.top * dpr),
        width: Math.round(domRect.width * dpr),
        height: Math.round(domRect.height * dpr),
      };
    };

    const reposition = async () => {
      const rect = await computeRect();
      if (!rect || cancelled) return;
      try {
        await browserPanelReposition(rect);
        if (!cancelled) setNativeDebug(`rect ${rect.x},${rect.y} ${rect.width}x${rect.height}`);
      } catch {
        // Panel window not open yet -- e.g. a resize firing before the
        // initial open() below has resolved. Nothing to reposition.
      }
    };

    (async () => {
      try {
        const rect = await computeRect();
        if (!rect || cancelled) return;
        await browserPanelOpen(rect);
        if (cancelled) return;
        setNativeDebug(`rect ${rect.x},${rect.y} ${rect.width}x${rect.height}`);
        const win = getCurrentWindow();
        unlistenMoved = await win.onMoved(() => void reposition());
        unlistenResized = await win.onResized(() => void reposition());
        if (containerRef.current) {
          resizeObserver = new ResizeObserver(() => void reposition());
          resizeObserver.observe(containerRef.current);
        }
      } catch (err) {
        if (!cancelled) setNativeError(err instanceof Error ? err.message : String(err));
      }
    })();

    return () => {
      cancelled = true;
      unlistenMoved?.();
      unlistenResized?.();
      resizeObserver?.disconnect();
      void browserPanelClose();
    };
    // See the WS effect above's own comment on including tauriMode here.
  }, [tauriMode]);

  // Browser panel, Electron migration Phase 2 -- real embedding, unlike
  // the Tauri effect above (still Stage 1, a placeholder with nothing
  // wired to a backend). A `WebContentsView` is a true child of the main
  // window, not a synced sibling top-level window like Tauri's own
  // approach -- `setBounds()` takes coordinates relative to the parent
  // window's own content area, so computeRect here is just
  // getBoundingClientRect() with no devicePixelRatio/innerPosition() math
  // at all (contrast with the Tauri effect's own computeRect above), and
  // the main window *moving* needs no reposition call (the child's
  // position relative to its own parent doesn't change when the parent
  // moves). Two independent things can still change where this panel's
  // own container sits *within* the window, so both are watched: a plain
  // `window.resize` listener (the window's overall width changing moves
  // this panel's left edge even when the panel's own on-screen size
  // doesn't change at all, since it's docked to the right edge of a flex
  // row -- a ResizeObserver on containerRef alone would miss exactly this
  // case, since ResizeObserver only fires on the observed element's own
  // size changing) and the same ResizeObserver on containerRef the non-
  // electron canvas path below already uses for its own purposes (the
  // resize-handle drag, or the nav rail collapsing, both change the
  // container's own size directly).
  useEffect(() => {
    if (!electronMode) return;
    let cancelled = false;
    let resizeObserver: ResizeObserver | undefined;

    const computeRect = (): BrowserPanelRect | null => {
      const container = containerRef.current;
      if (!container) return null;
      const rect = container.getBoundingClientRect();
      return {
        x: Math.round(rect.left),
        y: Math.round(rect.top),
        width: Math.round(rect.width),
        height: Math.round(rect.height),
      };
    };

    const reposition = () => {
      const rect = computeRect();
      if (!rect || cancelled) return;
      void electronBrowserPanelReposition(rect);
    };

    (async () => {
      const rect = computeRect();
      if (!rect || cancelled) return;
      await electronBrowserPanelOpen(rect);
      if (cancelled) return;
      window.addEventListener("resize", reposition);
      if (containerRef.current) {
        resizeObserver = new ResizeObserver(reposition);
        resizeObserver.observe(containerRef.current);
      }
    })();

    onBrowserPanelNavigated((payload) => {
      setElectronUrl(payload.url);
      setElectronAddressValue(payload.url);
      setElectronCanGoBack(payload.canGoBack);
      setElectronCanGoForward(payload.canGoForward);
      setElectronLoadError(null);
    });
    onBrowserPanelLoadError((errorDescription) => {
      setElectronLoadError(errorDescription);
    });
    onBrowserPanelPicked((payload) => {
      setPicked(payload);
      // One pick exits pick mode, same as the non-desktop canvas path's
      // own WS "picked" handler -- the effect below reacting to pickMode
      // then tells the content script to stop highlighting.
      setPickMode(false);
    });

    return () => {
      cancelled = true;
      window.removeEventListener("resize", reposition);
      resizeObserver?.disconnect();
      void electronBrowserPanelClose();
    };
    // See the WS effect above's own comment on including tauriMode/
    // electronMode here.
  }, [electronMode]);

  // Element-picking, Electron migration Phase 3: forwards pickMode's own
  // toggle down to the panel's content script, which draws the highlight
  // itself (see browserPanelContent.ts). Deliberately its own effect,
  // separate from the open/reposition one above -- that one only ever
  // runs once per mount ([electronMode] doesn't change), while pickMode
  // toggles repeatedly over the component's lifetime.
  useEffect(() => {
    if (!electronMode) return;
    void electronBrowserPanelSetPickMode(pickMode);
  }, [electronMode, pickMode]);

  // Tracks this panel's own on-screen display size -- purely a local
  // rendering concern now (the canvas's own CSS box + backing pixel
  // buffer, see the render below), decoupled from the remote page's own
  // emulated viewport (remoteViewportSize, sent once on connect above,
  // not here). Dragging the panel wider/narrower or resizing the window
  // no longer round-trips to the backend at all -- the canvas simply
  // displays more or less of the same real-resolution remote render via
  // ctx.drawImage's own resampling (drawFrame below), the same way
  // zooming a remote-desktop viewer's window doesn't need to ask the
  // remote machine to re-render at a new resolution. This is strictly
  // less backend chatter than before (which debounced a resize message
  // per drag tick -- still a real round trip, just throttled); now
  // there's none at all for this specific interaction.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const observer = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect;
      const next = { width: Math.round(width), height: Math.round(height) };
      if (next.width <= 0 || next.height <= 0) return;
      if (next.width === canvasSizeRef.current.width && next.height === canvasSizeRef.current.height) return;
      canvasSizeRef.current = next;
      setCanvasSize(next);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // Select mode is a distinct interaction layer over the live page (see
  // onCanvasMouseMove below) -- leaving it clears whatever was still
  // highlighted rather than letting a stale box linger.
  useEffect(() => {
    if (!pickMode) setHoverElement(null);
  }, [pickMode]);

  // Drag-to-resize: the handle sits on the panel's own left edge (it's
  // docked to the right of the window), so dragging it left/right just
  // means "how far is the pointer from the window's right edge" -- no
  // need to track a drag-start offset. Listens on window, not the
  // handle itself, since the pointer routinely moves off a 4px-wide
  // strip mid-drag. This is the *only* thing that changes panelWidth;
  // the ResizeObserver effect above already reacts to the resulting
  // layout change and re-syncs the remote page's own viewport to match
  // -- no separate wiring needed here for that half.
  //
  // The ceiling is computed from the window's own current width, not a
  // fixed constant -- a hardcoded cap felt arbitrarily narrow on a large
  // monitor and already-binding on a small one (reported: "Cowork可以拉得
  // 很宽" -- its own panel isn't capped well short of the window either).
  useEffect(() => {
    const onMouseMove = (e: MouseEvent) => {
      if (!draggingRef.current) return;
      const next = Math.round(window.innerWidth - e.clientX);
      const maxWidth = Math.min(
        MAX_PANEL_WIDTH_ABSOLUTE,
        Math.max(MIN_PANEL_WIDTH, window.innerWidth - PANEL_WIDTH_WINDOW_MARGIN),
      );
      setPanelWidth(Math.min(maxWidth, Math.max(MIN_PANEL_WIDTH, next)));
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
    // Dragging over the live canvas mid-resize would otherwise select
    // its surrounding text/select the page behind it -- suppressed for
    // the duration of the drag only, same as most split-pane widgets.
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  };

  const navigate = () => {
    if (addressValue.trim()) send({ type: "navigate", url: addressValue.trim() });
  };

  // The remote page's own viewport is the user's real screen resolution
  // (remoteViewportSize), not this panel's own on-screen display size
  // (canvasSize) -- deliberately decoupled, see remoteViewportSize's own
  // comment. Scaling click/hover coordinates against the *display* rect
  // but the *remote* viewport size is exactly how any scaled-down remote
  // view translates a click back to full-resolution coordinates.
  const toRemoteCoords = (e: { clientX: number; clientY: number }): [number, number] => {
    const canvas = canvasRef.current;
    if (!canvas) return [0, 0];
    const rect = canvas.getBoundingClientRect();
    const viewport = remoteViewportSize();
    return [
      ((e.clientX - rect.left) / rect.width) * viewport.width,
      ((e.clientY - rect.top) / rect.height) * viewport.height,
    ];
  };

  // A drag gesture on the remote page (a scrollbar thumb, selecting
  // text, a slider) needs a real mousedown -> however-many-mousemoves ->
  // mouseup sequence with the button genuinely held the whole time --
  // real-reported as "can't drag a scrollbar incrementally, it just
  // jumps to the end" (clicking a scrollbar *track* without a real drag
  // is exactly a jump-to-that-position scroll in every browser, the
  // correct behavior for what was actually being sent). The previous
  // onCanvasClick sent mousePressed immediately followed by
  // mouseReleased on every click -- there was no way for a drag to ever
  // exist as far as the remote page's own event handling was concerned,
  // click or not. Fixed the same way the panel's own resize-handle drag
  // (onResizeHandleMouseDown, above) already works: mousedown starts it,
  // a window-level mouseup (not just the canvas's own) ends it so a drag
  // that leaves the canvas mid-gesture still completes correctly, same
  // as a real OS would report it.
  const mouseDownRef = useRef(false);

  // Same math as toRemoteCoords, callable from the window-level mouseup
  // effect below (registered once at mount) -- window.screen is a plain
  // global, not React state, so there's no stale-closure concern reading
  // it directly here the way canvasSizeRef exists to solve for canvasSize.
  const remoteCoordsFromRefs = (clientX: number, clientY: number): [number, number] => {
    const canvas = canvasRef.current;
    if (!canvas) return [0, 0];
    const rect = canvas.getBoundingClientRect();
    const viewport = remoteViewportSize();
    return [
      ((clientX - rect.left) / rect.width) * viewport.width,
      ((clientY - rect.top) / rect.height) * viewport.height,
    ];
  };

  const onCanvasMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (pickMode) return; // pick mode commits on click below, no drag involved
    const [x, y] = toRemoteCoords(e);
    mouseDownRef.current = true;
    send({ type: "mouse", kind: "mousePressed", x, y });
    // Moves keyboard focus onto the hidden IME-bridge input (see its own
    // comment near canvasRef above) and repositions it to roughly where
    // the press landed, so the OS's IME candidate window pops up near
    // where the user is about to type rather than in some fixed corner.
    const container = containerRef.current;
    if (container) {
      const rect = container.getBoundingClientRect();
      setImePos({ left: e.clientX - rect.left, top: e.clientY - rect.top });
    }
    imeInputRef.current?.focus();
  };

  const onCanvasClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!pickMode) return; // an ordinary click is already handled by mousedown/mouseup above
    const [x, y] = toRemoteCoords(e);
    send({ type: "pick_element", x, y });
  };

  const onCanvasMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const [x, y] = toRemoteCoords(e);
    if (pickMode) {
      // Throttled, not one hover_element per mousemove event -- each is
      // a real CDP round trip (Runtime.evaluate) relayed through a
      // single-message-at-a-time WS loop server-side (see app.py's
      // browser_panel_ws), so firing at full mousemove rate would queue
      // up requests faster than the backend can answer them and the
      // highlight box would visibly lag behind the cursor.
      const now = performance.now();
      if (now - lastHoverSentRef.current < 60) return;
      lastHoverSentRef.current = now;
      send({ type: "hover_element", x, y });
      return;
    }
    send({ type: "mouse", kind: "mouseMoved", x, y });
  };

  const onCanvasMouseLeave = () => {
    if (pickMode) setHoverElement(null);
  };

  const onCanvasWheel = (e: React.WheelEvent<HTMLCanvasElement>) => {
    const [x, y] = toRemoteCoords(e);
    send({ type: "mouse", kind: "mouseWheel", x, y, deltaX: e.deltaX, deltaY: e.deltaY });
  };

  // Ends a drag started by onCanvasMouseDown above -- listens on window,
  // not just the canvas, same reasoning as the panel's own resize-handle
  // drag: the pointer can leave the canvas mid-drag (dragging a
  // scrollbar thumb past the panel's own edge, e.g.) and the gesture
  // still needs to complete correctly rather than leaving mouseDownRef
  // stuck true forever. remoteCoordsFromRefs reads window.screen
  // directly (not React state), so this effect can safely register once
  // at mount with no dependency array to keep in sync.
  useEffect(() => {
    const onWindowMouseUp = (e: MouseEvent) => {
      if (!mouseDownRef.current) return;
      mouseDownRef.current = false;
      const [x, y] = remoteCoordsFromRefs(e.clientX, e.clientY);
      send({ type: "mouse", kind: "mouseReleased", x, y });
    };
    window.addEventListener("mouseup", onWindowMouseUp);
    return () => window.removeEventListener("mouseup", onWindowMouseUp);
  }, []);

  // Keyboard input all goes through the hidden IME-bridge <input> now
  // (imeInputRef, rendered near the bottom of this component), not the
  // canvas -- see imeInputRef's own comment for why a canvas can never
  // support IME composition at all, regardless of what its own keydown
  // handling does.
  //
  // Non-printable/control keys (Backspace/Enter/Tab/arrows/Escape) are
  // still forwarded here, from keydown, exactly as before -- but only
  // when not mid-composition (composingRef false), so Enter/Backspace/
  // arrows used to navigate or confirm IME candidates stay entirely
  // local to the OS input method instead of also reaching the remote
  // page. Plain printable single characters are deliberately NOT handled
  // here anymore (this used to send Input.insertText straight from
  // keydown) -- whether a given keypress turns out to be a standalone
  // character or the first letter of an IME composition can't be told
  // apart at keydown time (isComposing is still false for that very
  // first keystroke), so all printable text now flows through
  // onImeInput/onImeCompositionEnd below instead, which see the actual
  // outcome either way.
  const onImeKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (composingRef.current) return;
    if (e.key.length === 1) return;
    e.preventDefault();
    send({ type: "key", kind: "keyDown", key: e.key });
  };
  const onImeKeyUp = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (justEndedCompositionRef.current) {
      justEndedCompositionRef.current = false;
      return;
    }
    if (composingRef.current) return;
    if (e.key.length !== 1) send({ type: "key", kind: "keyUp", key: e.key });
  };
  const onImeCompositionStart = () => {
    composingRef.current = true;
  };
  const onImeCompositionEnd = (e: React.CompositionEvent<HTMLInputElement>) => {
    composingRef.current = false;
    justEndedCompositionRef.current = true;
    if (e.data) send({ type: "text", text: e.data });
    e.currentTarget.value = "";
  };
  // Fires for both a composed IME commit (ignored here -- already sent
  // by onImeCompositionEnd above, which also clears the field first) and
  // plain non-IME typing/paste, which is the actual case this handles:
  // grabs whatever just landed in the field (a single typed character in
  // the common case, a whole pasted string if the user pasted) and
  // forwards it in one Input.insertText call, then clears the field so
  // it never accumulates.
  const onImeInput = (e: React.FormEvent<HTMLInputElement>) => {
    if (composingRef.current) return;
    const value = e.currentTarget.value;
    if (value) {
      send({ type: "text", text: value });
      e.currentTarget.value = "";
    }
  };

  // Computed fresh on every render (not memoized) -- it's cheap
  // (getBoundingClientRect + arithmetic) and has to track the canvas's
  // real, current layout box, which a memo keyed on hoverElement/
  // pickMode alone could miss (e.g. a panel resize with no new hover
  // event yet).
  let highlightStyle: React.CSSProperties | null = null;
  let labelStyle: React.CSSProperties | null = null;
  let labelText = "";
  if (pickMode && hoverElement && canvasRef.current) {
    const canvas = canvasRef.current;
    const displayRect = canvas.getBoundingClientRect();
    // Same reasoning as toRemoteCoords above: hoverElement's own rect is
    // in the remote page's own CSS-pixel space, which is now the user's
    // real screen resolution (remoteViewportSize), not this panel's own
    // on-screen display size -- scale the (typically much larger) remote
    // rect down into the (typically much smaller) display box.
    const viewport = remoteViewportSize();
    const scaleX = viewport.width > 0 ? displayRect.width / viewport.width : 1;
    const scaleY = viewport.height > 0 ? displayRect.height / viewport.height : 1;
    const { rect, tag } = hoverElement;
    const left = rect.x * scaleX;
    const top = rect.y * scaleY;
    const width = rect.width * scaleX;
    const height = rect.height * scaleY;
    highlightStyle = { left, top, width, height };
    labelText = `${tag} ${Math.round(rect.width)}×${Math.round(rect.height)}`;
    // Sits just above the box by default, like a real DevTools inspector
    // tooltip -- flips below when the element itself is too close to the
    // canvas's own top edge for that to fit. Clamped into the canvas's
    // own bounds either way, not just flipped: a full-page element (e.g.
    // hovering the page's own <body>) has no "above" *or* "below" that
    // isn't already off both edges, and the container clips anything
    // past them (overflow-hidden) -- a real, caught-in-testing case, not
    // hypothetical.
    const labelHeight = 20;
    const aboveTop = top - labelHeight;
    const belowTop = top + height;
    const preferredTop = top >= labelHeight ? aboveTop : belowTop;
    labelStyle = {
      left: Math.max(0, Math.min(left, canvasSize.width - 4)),
      top: Math.max(0, Math.min(preferredTop, canvasSize.height - labelHeight)),
    };
  }

  return (
    <div
      className="relative flex h-full shrink-0 flex-col border-l border-[var(--border)] bg-[var(--panel-bg)]"
      style={{ width: panelWidth }}
    >
      <div
        onMouseDown={onResizeHandleMouseDown}
        title="Drag to resize"
        className="absolute -left-1 top-0 z-10 h-full w-2 cursor-col-resize hover:bg-[var(--accent)]/40"
      />
      <div className="flex items-center gap-2 border-b border-[var(--border)] px-3 py-2.5">
        <span className="text-sm font-medium">Browser</span>
        <div className="ml-auto flex items-center gap-1">
          <button type="button" className="text-[var(--muted)] hover:text-[var(--fg)]" title="Close" onClick={onClose}>
            <CloseIcon className="h-4 w-4" />
          </button>
        </div>
      </div>

      {tauriMode ? (
        // Stage 1 only (see the effect above): a real native window
        // renders directly on top of this placeholder once positioned,
        // so there is deliberately no toolbar/canvas/IME-bridge/pick-UI
        // here yet -- none of it is wired to any backend for this path
        // until Stage 2 (CDP attach + navigation) and Stage 3 (element
        // picking). The placeholder's own text is only ever visible
        // for the brief moment before the native window's first
        // reposition lands, or if that failed -- both worth being able
        // to see rather than a silent blank rectangle either way.
        <div ref={containerRef} className="relative min-h-0 flex-1 overflow-hidden bg-black/5">
          {nativeError ? (
            <div className="absolute inset-0 flex items-center justify-center px-4 text-center text-sm text-red-500">
              Native window failed to open: {nativeError}
            </div>
          ) : (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 text-center text-sm text-[var(--muted)]">
              <span>Native browser window</span>
              {nativeDebug && <span className="text-xs opacity-70">{nativeDebug}</span>}
            </div>
          )}
        </div>
      ) : electronMode ? (
        // A real, natively-embedded WebContentsView paints directly on
        // top of this empty div (see the effect above) -- no canvas, no
        // IME bridge, no synthetic input relay, all of that machinery
        // the non-desktop path below needs simply doesn't apply to a
        // true native child view. Element-picking (Phase 3): the
        // highlight itself is drawn inside the live page's own DOM by
        // browserPanelContent.ts, not here -- this side only toggles
        // pick mode on/off and receives the final committed pick (see
        // the pickMode-sync effect below and onBrowserPanelPicked).
        <>
          <div className="flex items-center gap-1.5 border-b border-[var(--border)] px-2.5 py-2">
            <button
              type="button"
              title="Back"
              disabled={!electronCanGoBack}
              className="rounded-md border border-[var(--border)] px-2 py-1 text-xs hover:bg-[var(--card-bg)] disabled:opacity-40"
              onClick={() => void electronBrowserPanelBack()}
            >
              ‹
            </button>
            <button
              type="button"
              title="Forward"
              disabled={!electronCanGoForward}
              className="rounded-md border border-[var(--border)] px-2 py-1 text-xs hover:bg-[var(--card-bg)] disabled:opacity-40"
              onClick={() => void electronBrowserPanelForward()}
            >
              ›
            </button>
            <input
              className="min-w-0 flex-1 rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-xs outline-none focus:border-[var(--accent)]"
              placeholder="Enter a URL..."
              value={electronAddressValue}
              onChange={(e) => setElectronAddressValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && electronAddressValue.trim()) {
                  void electronBrowserPanelNavigate(electronAddressValue.trim());
                }
              }}
            />
            <button
              type="button"
              className="rounded-md border border-[var(--border)] px-2 py-1 text-xs hover:bg-[var(--card-bg)]"
              onClick={() => electronAddressValue.trim() && void electronBrowserPanelNavigate(electronAddressValue.trim())}
            >
              Go
            </button>
            <button
              type="button"
              className="rounded-md border border-[var(--border)] px-2 py-1 text-xs hover:bg-[var(--card-bg)]"
              onClick={() => void electronBrowserPanelReload()}
            >
              ⟳
            </button>
            <button
              type="button"
              title="Select an element to send to the chat"
              className={`rounded-md border px-2 py-1 text-xs ${pickMode ? "border-[var(--accent)] bg-[var(--accent)] text-[var(--accent-fg)]" : "border-[var(--border)] hover:bg-[var(--card-bg)]"}`}
              onClick={() => setPickMode((v) => !v)}
            >
              Select
            </button>
          </div>

          {electronLoadError && (
            <div className="flex items-center justify-between gap-2 border-b border-[var(--border)] bg-red-500/10 px-2.5 py-1.5 text-xs text-red-500">
              <span>{electronLoadError}</span>
              <button type="button" className="shrink-0 hover:opacity-70" onClick={() => setElectronLoadError(null)}>
                <CloseIcon className="h-3 w-3" />
              </button>
            </div>
          )}

          <div ref={containerRef} className="relative min-h-0 flex-1 overflow-hidden bg-black/5">
            {/* Same caveat the Tauri placeholder above already documents:
             * the real WebContentsView is a separately-composited native
             * surface painting directly on top of this div once
             * positioned, so this text is only actually visible for the
             * brief moment before that happens (an empty view still
             * shows its own blank white background, covering this). Kept
             * anyway, same reasoning -- worth seeing during that instant
             * (or if opening somehow never lands) rather than a silent
             * empty rectangle either way. */}
            {!electronUrl && !electronLoadError && (
              <div className="absolute inset-0 flex items-center justify-center px-4 text-center text-sm text-[var(--muted)]">
                Enter a URL above to get started
              </div>
            )}
          </div>

          {picked && (
            <div className="flex flex-col gap-2 border-t border-[var(--border)] p-2.5">
              <img
                src={`data:image/jpeg;base64,${picked.screenshot}`}
                alt="Selected element"
                className="max-h-32 w-full rounded-md border border-[var(--border)] object-contain"
              />
              {picked.text && <p className="truncate text-xs text-[var(--muted)]">{picked.text}</p>}
              <div className="flex justify-end gap-2">
                <button type="button" className="rounded-md border border-[var(--border)] px-3 py-1 text-xs" onClick={() => setPicked(null)}>
                  Discard
                </button>
                <button
                  type="button"
                  className="rounded-md bg-[var(--accent)] px-3 py-1 text-xs text-[var(--accent-fg)]"
                  onClick={() => {
                    onSendToChat({
                      name: `${picked.tag || "element"}.jpg`,
                      dataUrl: `data:image/jpeg;base64,${picked.screenshot}`,
                      text: picked.text || undefined,
                      tag: picked.tag || undefined,
                    });
                    setPicked(null);
                  }}
                >
                  Add to chat
                </button>
              </div>
            </div>
          )}
        </>
      ) : (
        <>
          <div className="flex items-center gap-1.5 border-b border-[var(--border)] px-2.5 py-2">
            <button
              type="button"
              title="Back"
              className="rounded-md border border-[var(--border)] px-2 py-1 text-xs hover:bg-[var(--card-bg)]"
              onClick={() => send({ type: "back" })}
            >
              ‹
            </button>
            <button
              type="button"
              title="Forward"
              className="rounded-md border border-[var(--border)] px-2 py-1 text-xs hover:bg-[var(--card-bg)]"
              onClick={() => send({ type: "forward" })}
            >
              ›
            </button>
            <input
              className="min-w-0 flex-1 rounded-md border border-[var(--border)] bg-transparent px-2 py-1 text-xs outline-none focus:border-[var(--accent)]"
              placeholder="Enter a URL..."
              value={addressValue}
              onChange={(e) => setAddressValue(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && navigate()}
            />
            <button type="button" className="rounded-md border border-[var(--border)] px-2 py-1 text-xs hover:bg-[var(--card-bg)]" onClick={navigate}>
              Go
            </button>
            <button
              type="button"
              className="rounded-md border border-[var(--border)] px-2 py-1 text-xs hover:bg-[var(--card-bg)]"
              onClick={() => send({ type: "reload" })}
            >
              ⟳
            </button>
            <button
              type="button"
              title="Select an element to send to the chat"
              className={`rounded-md border px-2 py-1 text-xs ${pickMode ? "border-[var(--accent)] bg-[var(--accent)] text-[var(--accent-fg)]" : "border-[var(--border)] hover:bg-[var(--card-bg)]"}`}
              onClick={() => setPickMode((v) => !v)}
            >
              Select
            </button>
          </div>

          {actionError && (
            <div className="flex items-center justify-between gap-2 border-b border-[var(--border)] bg-red-500/10 px-2.5 py-1.5 text-xs text-red-500">
              <span>{actionError}</span>
              <button type="button" className="shrink-0 hover:opacity-70" onClick={() => setActionError(null)}>
                <CloseIcon className="h-3 w-3" />
              </button>
            </div>
          )}

          <div ref={containerRef} className="relative min-h-0 flex-1 overflow-hidden bg-black/5">
            {status === "connecting" && (
              <div className="absolute inset-0 flex items-center justify-center text-sm text-[var(--muted)]">Starting browser&hellip;</div>
            )}
            {status === "error" && (
              <div className="absolute inset-0 flex items-center justify-center px-4 text-center text-sm text-red-500">
                {errorText ?? "Browser connection lost."}
              </div>
            )}
            <canvas
              ref={canvasRef}
              // width/height (the backing pixel buffer) are scaled up by
              // devicePixelRatio for a sharp image on a high-DPI display --
              // reported live as "分辨率很低" (looks low-resolution)
              // otherwise, since the remote page was rendering at this
              // panel's CSS pixel size with no DPI awareness at all, which
              // Windows' own display scaling (125-200% is completely
              // ordinary) then stretches back up to fill the same box.
              // style.width/height (below) are left at the *unscaled*
              // canvasSize on purpose, in real CSS pixels -- explicit, not
              // the earlier w-full/h-full percentage. A canvas is a
              // "replaced element" in CSS layout terms (like <img>), and
              // its width/height *attributes* (its intrinsic content size)
              // can leak into a flex ancestor's own sizing in some engines
              // unless every explicit CSS size in the chain is unambiguous
              // -- reported live as the panel only ever showing part of the
              // page with a stray horizontal scrollbar appearing once the
              // attributes here grew large enough (this DPI change made
              // them larger still). Explicit numeric style removes the
              // ambiguity outright instead of chasing it through every flex
              // container up the tree.
              width={Math.round(canvasSize.width * (window.devicePixelRatio || 1))}
              height={Math.round(canvasSize.height * (window.devicePixelRatio || 1))}
              style={{ width: canvasSize.width, height: canvasSize.height }}
              className={`outline-none ${pickMode ? "cursor-crosshair" : ""}`}
              onClick={onCanvasClick}
              onMouseDown={onCanvasMouseDown}
              onMouseMove={onCanvasMouseMove}
              onMouseLeave={onCanvasMouseLeave}
              onWheel={onCanvasWheel}
            />
            {/* Invisible IME-bridge input -- see imeInputRef's own comment
             * above for why this exists at all. opacity: 0 (not display:none
             * or visibility:hidden, both of which would also make it
             * unfocusable and un-composable) keeps it truly present so the OS
             * input method attaches to it normally; pointerEvents: "none"
             * keeps it out of the way of clicks meant for the canvas
             * underneath. Repositioned to the last click (onCanvasClick) so
             * the IME candidate window opens near where the user is about to
             * type instead of a fixed corner. */}
            <input
              ref={imeInputRef}
              data-testid="browser-ime-bridge"
              className="absolute h-5 w-1 border-none bg-transparent p-0 opacity-0 outline-none"
              style={{ left: imePos.left, top: imePos.top, pointerEvents: "none" }}
              autoComplete="off"
              autoCorrect="off"
              autoCapitalize="off"
              spellCheck={false}
              onKeyDown={onImeKeyDown}
              onKeyUp={onImeKeyUp}
              onCompositionStart={onImeCompositionStart}
              onCompositionEnd={onImeCompositionEnd}
              onInput={onImeInput}
            />
            {highlightStyle && (
              <div
                className="pointer-events-none absolute border-2 border-[var(--accent)] bg-[var(--accent)]/10"
                style={highlightStyle}
              />
            )}
            {labelStyle && (
              <div
                className="pointer-events-none absolute whitespace-nowrap rounded bg-[var(--accent)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--accent-fg)]"
                style={labelStyle}
              >
                {labelText}
              </div>
            )}
          </div>

          {picked && (
            <div className="flex flex-col gap-2 border-t border-[var(--border)] p-2.5">
              <img
                src={`data:image/jpeg;base64,${picked.screenshot}`}
                alt="Selected element"
                className="max-h-32 w-full rounded-md border border-[var(--border)] object-contain"
              />
              {picked.text && <p className="truncate text-xs text-[var(--muted)]">{picked.text}</p>}
              <div className="flex justify-end gap-2">
                <button type="button" className="rounded-md border border-[var(--border)] px-3 py-1 text-xs" onClick={() => setPicked(null)}>
                  Discard
                </button>
                <button
                  type="button"
                  className="rounded-md bg-[var(--accent)] px-3 py-1 text-xs text-[var(--accent-fg)]"
                  onClick={() => {
                    onSendToChat({
                      name: `${picked.tag || "element"}.jpg`,
                      dataUrl: `data:image/jpeg;base64,${picked.screenshot}`,
                      text: picked.text || undefined,
                      tag: picked.tag || undefined,
                    });
                    setPicked(null);
                  }}
                >
                  Add to chat
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function drawFrame(canvas: HTMLCanvasElement | null, base64Jpeg: string): void {
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const img = new Image();
  img.onload = () => ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
  img.src = `data:image/jpeg;base64,${base64Jpeg}`;
}
