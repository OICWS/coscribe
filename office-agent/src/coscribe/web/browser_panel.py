"""The Browser panel's live view -- a real, visible-to-the-user embedded
browser (navigate, click, type, pick an element to send into the chat),
not the agent's own tool-driven automation (that's the separate
`playwright` MCP connector, launched by the model, never shown to the
user live). This is a human-facing "show and tell the AI what you mean"
surface: point at something on a real page instead of describing it in
words.

**Raw Chrome DevTools Protocol over a plain WebSocket, not the
`playwright` Python package.** Deliberate: this only needs a thin slice
of what Playwright offers (navigate, click, type, screenshot, one CDP
domain's screencast), and the `playwright` package is a genuinely heavy
addition -- its own bundled Node.js driver process is a known, real
PyInstaller-packaging pain point (needs explicit `--collect-all
playwright` handling to bundle correctly, easy to get wrong and only
discover on the frozen Windows build, not locally). CDP itself is a
stable, well-documented plain JSON-RPC-over-WebSocket protocol; `httpx`
(the HTTP handshake) and `websockets` (the CDP connection itself) are
already real dependencies here (the latter transitively via
`uvicorn[standard]`, now declared directly -- see pyproject.toml), so
this adds no new dependency surface at all.

Reuses the exact same browser executable the Playwright MCP connector's
own "no browser found" flow already resolves on Windows
(`web/browser_detect.py`'s `find_windows_browser`) -- launched headless
(`--headless=new`) with its own throwaway `--user-data-dir`, so it never
touches the user's real browser profile and never pops up a second,
separate window alongside coscribe's own panel; the panel's `<canvas>`
*is* the only visible surface for it.

**Deliberately narrow scope for v1** -- explicitly not built, not
half-built: no "Import cookies" (reading a real browser's cookie store
is genuinely sensitive -- OS-level access, per-browser decryption on
Chrome/Edge -- a separate, larger piece of work than this pass covers),
no "Annotate" freehand-drawing tool (a canvas-overlay feature orthogonal
to the live view itself). What *is* built: live view, mouse/keyboard
input (enough to fill in a form or click through a login), back/forward/
reload history navigation (`go_back`/`go_forward`/`reload` -- CDP has no
direct Page.goBack, built on Page.getNavigationHistory +
Page.navigateToHistoryEntry instead), and "select an element" -- click
in pick mode, get a cropped screenshot + the element's text, handed to
the frontend to attach to the next chat message.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import shutil
import socket
import sys
import tempfile
from pathlib import Path
from typing import Any, NamedTuple

import httpx
import websockets
from websockets.asyncio.client import ClientConnection

from ..runtime.proxy import configured_proxy
from .browser_detect import find_windows_browser

logger = logging.getLogger(__name__)

# Matches the synchronous scripts tools' own already-battle-tested
# balance (see tools/scripts.py) -- long enough for a real page load over
# a slow connection, short enough that a hung launch doesn't leave the
# WS handler waiting forever.
_LAUNCH_TIMEOUT = 20.0
_CDP_CALL_TIMEOUT = 15.0
# Padding applied when growing the real headless Chrome window to fit a
# requested viewport (see _ensure_real_window_at_least) -- covers
# window-chrome/decoration overhead Browser.setWindowBounds's own
# width/height don't account for (confirmed live: exactly matching the
# target size still left the usable area short), generously rather than
# trying to compute the exact overhead.
_WINDOW_SIZE_MARGIN = 200


class _KeyDefinition(NamedTuple):
    code: str
    vk_code: int


# The non-printable keys BrowserPanel.tsx's onCanvasKeyDown actually
# forwards here (everything with e.key.length !== 1 -- a single
# printable character goes through insert_text instead, see
# dispatch_key's own docstring). `code` is the physical-key value Chrome
# also reports on a real KeyboardEvent (some page scripts key off `code`
# instead of `key`); `vk_code` is the Windows virtual-key code CDP uses
# to decide the key's default action -- confirmed against Puppeteer's own
# CDP keyboard implementation, not guessed.
_KEY_DEFINITIONS: dict[str, _KeyDefinition] = {
    "Backspace": _KeyDefinition("Backspace", 0x08),
    "Tab": _KeyDefinition("Tab", 0x09),
    "Enter": _KeyDefinition("Enter", 0x0D),
    "Escape": _KeyDefinition("Escape", 0x1B),
    "Delete": _KeyDefinition("Delete", 0x2E),
    "Home": _KeyDefinition("Home", 0x24),
    "End": _KeyDefinition("End", 0x23),
    "PageUp": _KeyDefinition("PageUp", 0x21),
    "PageDown": _KeyDefinition("PageDown", 0x22),
    "ArrowLeft": _KeyDefinition("ArrowLeft", 0x25),
    "ArrowUp": _KeyDefinition("ArrowUp", 0x26),
    "ArrowRight": _KeyDefinition("ArrowRight", 0x27),
    "ArrowDown": _KeyDefinition("ArrowDown", 0x28),
}


def _free_port() -> int:
    """Same "bind port 0, read back what the OS actually assigned" trick
    office-agent-desktop's own free_port() (lib.rs) uses -- avoids a
    fixed port colliding with anything else already running."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    """Reads a JPEG's real pixel width/height straight out of its own
    SOF (Start Of Frame) marker, without decoding the whole image --
    used by BrowserPanelSession._read_loop to catch a screencast frame
    that doesn't actually match the viewport size we asked for. See
    that call site's own comment for why this check exists at all: a
    real, reproducible Chromium/CDP quirk where screencast frames can
    silently revert to some smaller, unrelated size around a
    navigation, even after every explicit attempt to resync at the
    right moment (Page.loadEventFired included) -- self-healing
    detection turned out to be the only fix that actually held up
    under repeated live testing, not a specific one-shot resync timed
    to any single CDP event."""
    i = 2
    while i < len(data) - 9:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC2):
            height = (data[i + 5] << 8) + data[i + 6]
            width = (data[i + 7] << 8) + data[i + 8]
            return width, height
        length = (data[i + 2] << 8) + data[i + 3]
        i += 2 + length
    return None


def find_browser_executable() -> str | None:
    """Best-effort local Chromium-family browser lookup for the panel's
    own launch, one indirection layer above browser_detect.py's own
    Windows-only find_windows_browser: production (the packaged desktop
    app) is Windows-only (this project's own standing constraint, see
    ROADMAP.md), so that's the real path. `COSCRIBE_BROWSER_PANEL_EXE`
    is a plain escape hatch for developing/testing this module itself
    off Windows -- not a supported end-user setting, undocumented in
    .env.example on purpose."""
    override = os.environ.get("COSCRIBE_BROWSER_PANEL_EXE")
    if override and Path(override).is_file():
        return override
    if sys.platform == "win32":
        return find_windows_browser()
    return None


class BrowserPanelError(RuntimeError):
    """Raised for anything the WS handler should report back to the
    client as a plain {"type": "error", ...} message rather than letting
    propagate into an unhandled-exception WS close."""


class BrowserPanelSession:
    """One headless browser process + one CDP WebSocket connection to its
    single page target, for the lifetime of one Browser-panel WS
    connection. Not thread_id-scoped, not persisted -- a companion,
    ephemeral tool surface, not conversation state; closing the panel (or
    the WS dropping) kills the browser process, see close()."""

    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._ws: ClientConnection | None = None
        self._profile_dir: Path | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._on_frame: Any = None  # set by start_screencast
        self._viewport_width = 1280
        self._viewport_height = 800
        self._viewport_scale = 1.0  # devicePixelRatio, see resize()
        self._load_event: asyncio.Event | None = None  # see _wait_for_load
        self._resyncing = False  # see _read_loop's frame-size check

    async def launch(self) -> None:
        executable = find_browser_executable()
        if executable is None:
            raise BrowserPanelError(
                "No Chromium-family browser found (Edge/Chrome) -- the Browser panel "
                "needs one of these installed, same requirement as the Playwright connector."
            )
        port = _free_port()
        self._profile_dir = Path(tempfile.mkdtemp(prefix="coscribe_browser_panel_"))
        args = [
            executable,
            f"--remote-debugging-port={port}",
            "--headless=new",
            f"--user-data-dir={self._profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            # Just a starting size, grown dynamically later if needed --
            # headless Chrome's own compositor surface (what
            # Page.startScreencast/captureScreenshot actually reads
            # pixels from) is bounded by the real browser *window* size,
            # which a later Emulation.setDeviceMetricsOverride does NOT
            # resize -- that call only changes what the page itself
            # believes its viewport is (window.innerWidth,
            # elementFromPoint, layout), leaving CSS logic and captured
            # pixels silently disagreeing the moment the emulated size
            # exceeds this window's real one. Caught live: a page emulated
            # at 439x816 was screencasting frames only 439x218 tall (the
            # real window's own height, clipping the rest), while
            # elementFromPoint correctly used the full 816 -- exactly the
            # kind of "looks fine, coordinates are wrong" bug a visual
            # screenshot alone won't reveal. Used to be a fixed size,
            # deliberately never resized after launch, on the reasoning
            # that this was already "comfortably above" the only thing
            # that could ever be asked of it (BrowserPanel.tsx's own
            # on-screen panel width) -- no longer true once resize() can
            # be asked for the user's real screen resolution instead (see
            # remoteViewportSize in BrowserPanel.tsx), which plenty of
            # ordinary monitors already exceed this. resize() now grows
            # the real window to fit via _ensure_real_window_at_least
            # when needed; this launch size is just the reasonable
            # common-case starting point, not an assumed ceiling anymore.
            "--window-size=1920,1200",
        ]
        # Chrome/Edge's own zygote refuses to start as root at all without
        # this (real, hit in this exact sandbox during development -- see
        # ROADMAP.md's own write-up). Gated on actually running as root
        # (never true for a normal Windows desktop-app launch, this
        # project's real target), not a blanket flag -- --no-sandbox is a
        # real defense-in-depth loss, not something to disable
        # unconditionally just because one dev/CI environment happens to
        # run as root.
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            args.append("--no-sandbox")
        # Chromium's own env-var-based proxy auto-detection is platform-
        # dependent -- reliable enough on Linux, but on Windows (this
        # project's actual target) it normally needs a real system/
        # registry proxy setting instead, not a bare inherited
        # HTTP_PROXY/HTTPS_PROXY. Pass it explicitly via --proxy-server
        # so a corporate-proxy .env setting reaches this headless
        # instance the same way it already reaches the LLM provider SDKs
        # (see runtime/proxy.py, also used by tools/websearch.py).
        proxy = configured_proxy()
        if proxy:
            args.append(f"--proxy-server={proxy}")
        args.append("about:blank")
        self._process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        target_url = await self._wait_for_page_target(port)
        self._ws = await websockets.connect(target_url, max_size=None)
        self._reader_task = asyncio.create_task(self._read_loop())
        await self.send("Page.enable")
        await self.send("Runtime.enable")
        await self._apply_device_metrics(self._viewport_width, self._viewport_height)

    async def _wait_for_page_target(self, port: int) -> str:
        deadline = asyncio.get_event_loop().time() + _LAUNCH_TIMEOUT
        last_error: Exception | None = None
        async with httpx.AsyncClient() as client:
            while asyncio.get_event_loop().time() < deadline:
                if self._process is not None and self._process.returncode is not None:
                    raise BrowserPanelError(
                        f"Browser process exited immediately (code {self._process.returncode})"
                    )
                try:
                    resp = await client.get(f"http://127.0.0.1:{port}/json/list", timeout=1.0)
                    targets = resp.json()
                    pages = [t for t in targets if t.get("type") == "page"]
                    if pages:
                        return str(pages[0]["webSocketDebuggerUrl"])
                except (httpx.ConnectError, httpx.TimeoutException) as exc:
                    last_error = exc
                await asyncio.sleep(0.2)
        raise BrowserPanelError(f"Browser never became reachable on port {port}: {last_error}")

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                try:
                    await self._handle_read_loop_message(raw)
                except Exception:
                    # This loop is the *only* thing that ever reads off the
                    # CDP socket -- every pending send() future, every
                    # screencast frame, every load-event wait, all of it
                    # goes through here. Letting one bad message (a
                    # malformed frame, an unexpected shape) propagate out
                    # of the `async for` would kill this whole task, and
                    # with it every one of those, permanently: no future
                    # send() could ever resolve again, frames stop arriving
                    # (CDP halts screencast until it sees an ack this loop
                    # would no longer send), and the outer WS handler
                    # eventually sees nothing but silence -- exactly the
                    # shape of "Browser connection lost" showing up for a
                    # session that never actually failed at the CDP
                    # transport level. Logged and skipped instead: whatever
                    # this one message was, the rest of the session
                    # shouldn't die for it.
                    logger.exception("browser_panel: error handling a CDP message, continuing")
        except websockets.exceptions.ConnectionClosed:
            pass

    async def _handle_read_loop_message(self, raw: str | bytes) -> None:
        message = json.loads(raw)
        if "id" in message:
            future = self._pending.pop(message["id"], None)
            if future is not None and not future.done():
                future.set_result(message)
        elif message.get("method") == "Page.screencastFrame" and self._on_frame is not None:
            params = message["params"]
            # Ack first: CDP stops sending further frames until the
            # previous one is acknowledged (backpressure built into
            # the protocol itself, not something this module has to
            # implement by hand). Fire-and-forget (_write, not
            # send()) is load-bearing, not an optimization: this
            # loop is the *only* thing that ever reads a response
            # off the socket, so calling send() here -- which
            # awaits its own response -- would deadlock this exact
            # loop against itself. Caught live via a real hang in
            # this module's own smoke test before it ever shipped.
            await self._write("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
            self._check_frame_size(params["data"])
            await self._on_frame(params["data"])
        elif message.get("method") == "Page.loadEventFired":
            if self._load_event is not None:
                self._load_event.set()

    def _check_frame_size(self, b64_data: str) -> None:
        """Called for every incoming screencast frame -- if its real
        JPEG dimensions don't match the viewport we last asked for,
        schedules a fire-and-forget resync (reapply Emulation.
        setDeviceMetricsOverride, restart the screencast) rather than
        awaiting one here directly (this runs inside _read_loop itself;
        an awaited round trip here would deadlock it the same way
        _write exists to avoid for the frame-ack, see that method's own
        comment). _resyncing guards against piling up overlapping
        resync tasks while one is already in flight -- each one already
        fixes every subsequent frame, a second wouldn't help and would
        just add more stop/restart churn."""
        if self._resyncing:
            return
        size = _jpeg_size(base64.b64decode(b64_data))
        expected = (
            round(self._viewport_width * self._viewport_scale),
            round(self._viewport_height * self._viewport_scale),
        )
        if size is None or size == expected:
            return
        self._resyncing = True
        asyncio.create_task(self._resync_mismatched_screencast())

    async def _resync_mismatched_screencast(self) -> None:
        try:
            await self._apply_device_metrics(
                self._viewport_width, self._viewport_height, self._viewport_scale
            )
            await self._restart_screencast_if_running()
        finally:
            self._resyncing = False

    async def _write(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Fire-and-forget: writes the request, registers no pending
        future, never awaits a response -- see _read_loop's own comment
        for why this has to exist separately from send() below."""
        if self._ws is None:
            raise BrowserPanelError("Session not launched")
        request_id = self._next_id
        self._next_id += 1
        payload: dict[str, Any] = {"id": request_id, "method": method}
        if params:
            payload["params"] = params
        await self._ws.send(json.dumps(payload))

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._ws is None:
            raise BrowserPanelError("Session not launched")
        request_id = self._next_id
        self._next_id += 1
        future: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future
        payload: dict[str, Any] = {"id": request_id, "method": method}
        if params:
            payload["params"] = params
        await self._ws.send(json.dumps(payload))
        try:
            response = await asyncio.wait_for(future, timeout=_CDP_CALL_TIMEOUT)
        finally:
            self._pending.pop(request_id, None)
        if "error" in response:
            raise BrowserPanelError(f"{method} failed: {response['error']}")
        result: dict[str, Any] = response.get("result", {})
        return result

    async def _apply_device_metrics(self, width: int, height: int, scale: float = 1.0) -> None:
        self._viewport_width = width
        self._viewport_height = height
        self._viewport_scale = scale
        await self.send(
            "Emulation.setDeviceMetricsOverride",
            {"width": width, "height": height, "deviceScaleFactor": scale, "mobile": False},
        )

    async def _start_screencast_at_current_size(self) -> None:
        # maxWidth/maxHeight pinned to the current emulated viewport is
        # load-bearing, not an optimization: CDP's screencast frames do
        # NOT automatically track Emulation.setDeviceMetricsOverride --
        # confirmed live, the hard way. Without an explicit cap here,
        # frames kept arriving at whatever resolution the *first-ever*
        # Page.startScreencast call happened to capture (a browser-
        # internal size unrelated to any viewport we asked for, observed
        # as 780x441 against a requested 439x816 -- not even the same
        # aspect ratio), while document.elementFromPoint and every other
        # CDP call correctly used the real, current viewport. drawFrame's
        # own ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
        # forces every frame to fill the canvas regardless of its native
        # size, so this silently LOOKED fine (no visible squashing) while
        # every click/hover coordinate translated through that canvas was
        # quietly wrong -- a real bug caught only by comparing a picked
        # element's own CDP-reported rect against where it visually
        # rendered, not by looking at a screenshot.
        await self.send(
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": 80,
                "everyNthFrame": 1,
                "maxWidth": round(self._viewport_width * self._viewport_scale),
                "maxHeight": round(self._viewport_height * self._viewport_scale),
            },
        )

    async def start_screencast(self, on_frame: Any) -> None:
        """`on_frame` is an async callable taking the raw base64 jpeg
        string for each frame -- called from inside _read_loop, so it must
        stay fast (the WS handler just forwards the string straight to
        the client, no re-encoding)."""
        self._on_frame = on_frame
        await self._start_screencast_at_current_size()

    async def _stop_screencast_if_running(self) -> None:
        if self._on_frame is not None:
            await self.send("Page.stopScreencast")

    async def _start_screencast_if_previously_running(self) -> None:
        if self._on_frame is not None:
            await self._start_screencast_at_current_size()

    async def _restart_screencast_if_running(self) -> None:
        """Stops and restarts the screencast at the currently-tracked
        viewport size -- used by resize() (a genuine new size). No-op
        if start_screencast hasn't run yet: resize() can otherwise race
        ahead of it, since the frontend's ResizeObserver can fire and
        send its first `resize` message before the backend's own
        start_screencast call (itself gated behind the slower launch())
        has run at all -- nothing to restart yet in that case.
        navigate()/reload() don't use this -- see _navigate_and_resync's
        own docstring for why they need the stop and start split apart
        around a Page.loadEventFired wait, not bundled back-to-back."""
        await self._stop_screencast_if_running()
        await self._start_screencast_if_previously_running()

    async def _ensure_real_window_at_least(self, width: int, height: int) -> None:
        """Grows the *real* headless Chrome window (not just the emulated
        viewport) to comfortably fit `width`x`height`, if it isn't
        already that big -- see launch()'s own `--window-size` comment
        for the underlying reason this exists at all: the screencast's
        actual compositor surface is bounded by the real window, which
        Emulation.setDeviceMetricsOverride does not resize on its own.
        That fixed `--window-size=1920,1200` launch arg was sized
        "comfortably above" the old ceiling on what a caller could ever
        ask for (this panel's own on-screen width) -- but now that
        resize() can be asked for the user's real screen resolution
        (BrowserPanel.tsx's remoteViewportSize, potentially larger than
        1920x1200 on plenty of ordinary monitors), that fixed launch
        size is no longer guaranteed to be big enough, and the *exact
        same* clipping bug the launch-arg comment describes reproduces
        for the live view -- confirmed live in this sandbox: a screencast
        frame requested at 2560x1440 against the unchanged 1920x1200
        window came back clipped to 1920x1061, not the requested size.

        Browser.setWindowBounds's width/height are the *outer* window
        bounds, not the exact usable viewport -- also confirmed live:
        asking for exactly the target size still left the screencast
        ~140px short in height (window chrome/decoration overhead, even
        headless). `_WINDOW_SIZE_MARGIN` pads past that rather than
        trying to compute the exact overhead, which isn't guaranteed
        stable across platforms/Chrome versions -- same "generously
        large, not exact" reasoning the original fixed launch size
        already used, just applied dynamically now instead of once at
        launch. Cheap to call unconditionally: resize() only runs once
        per session in practice (the remote viewport no longer changes
        just because the panel itself is dragged wider/narrower -- see
        BrowserPanel.tsx), not a hot path."""
        win = await self.send("Browser.getWindowForTarget")
        window_id = win.get("windowId")
        if window_id is None:
            return
        bounds = win.get("bounds", {})
        current_width = bounds.get("width", 0)
        current_height = bounds.get("height", 0)
        if current_width >= width and current_height >= height:
            return
        await self.send(
            "Browser.setWindowBounds",
            {
                "windowId": window_id,
                "bounds": {
                    "width": max(current_width, width + _WINDOW_SIZE_MARGIN),
                    "height": max(current_height, height + _WINDOW_SIZE_MARGIN),
                },
            },
        )

    async def resize(self, width: int, height: int, scale: float = 1.0) -> None:
        """Re-applies Emulation.setDeviceMetricsOverride to match the
        requested viewport size -- the panel's own on-screen size once
        (harmless if unchanged; superseded by the user's real screen
        resolution as of BrowserPanel.tsx's remoteViewportSize, sent
        once on connect). Without this the remote page stays locked to
        launch()'s fixed 1280x800 landscape viewport forever. Also
        restarts the screencast at the new size -- see
        _restart_screencast_if_running's own docstring for why CDP
        leaves no other way to do that. Grows the real window first if
        needed -- see _ensure_real_window_at_least's own docstring.

        `scale` is the frontend's own `window.devicePixelRatio`, forced
        through as CDP's `deviceScaleFactor` instead of the previous
        hardcoded 1 -- without it, a real high-DPI Windows display (a
        very ordinary 125-200% scaling setup) renders this same CSS-
        pixel-sized viewport at a *lower* physical pixel count than the
        OS then stretches it back up to fill, reported live as "看起来
        分辨率很低" (looks low-resolution) even though nothing was
        actually squashed -- a sharpness problem, not the earlier
        aspect-ratio one."""
        width = max(200, min(width, 6000))
        height = max(200, min(height, 6000))
        scale = max(0.5, min(scale, 4.0))
        await self._ensure_real_window_at_least(width, height)
        await self._apply_device_metrics(width, height, scale)
        await self._restart_screencast_if_running()

    async def _element_at(self, x: float, y: float) -> dict[str, Any] | None:
        """Shared `document.elementFromPoint` lookup behind both
        hover_element (cheap, called continuously while the pointer
        moves) and pick_element (the same lookup plus a cropped
        screenshot, called once on click) -- kept as one JS string so
        the two can never drift apart on what counts as "the element at
        this point". x/y are forced through float() before landing in
        the JS string -- this is string-built, not passed as a real
        argument (Runtime.evaluate has no argument-binding, unlike
        Runtime.callFunctionOn), so this is the injection guard, not
        just a type coercion."""
        js = f"""
        (function(x, y) {{
          const el = document.elementFromPoint(x, y);
          if (!el) return null;
          const rect = el.getBoundingClientRect();
          if (rect.width <= 0 || rect.height <= 0) return null;
          return {{
            tag: el.tagName.toLowerCase(),
            text: (el.innerText || el.textContent || "").trim().slice(0, 300),
            rect: {{ x: rect.x, y: rect.y, width: rect.width, height: rect.height }},
          }};
        }})({float(x)}, {float(y)})
        """
        result = await self.send("Runtime.evaluate", {"expression": js, "returnByValue": True})
        value: dict[str, Any] | None = result.get("result", {}).get("value")
        return value

    async def hover_element(self, x: float, y: float) -> dict[str, Any] | None:
        """Same lookup pick_element uses, minus the screenshot -- called
        continuously while the pointer moves in Select mode so the
        frontend can draw a live highlight box over whatever's currently
        under the cursor, the same hover-to-preview a real DevTools
        element inspector gives you before you commit a click. Returns
        None (not a BrowserPanelError) when nothing's under the point --
        a miss here is routine (the cursor crossing empty page
        background), not exceptional the way it is in pick_element's own
        "you clicked but there's nothing there" case."""
        return await self._element_at(x, y)

    async def _wait_for_load(self, timeout: float = 10.0) -> None:
        """Waits for the next Page.loadEventFired CDP event -- see
        _navigate_and_resync's own docstring for why navigate()/reload()
        need to know exactly when the new page has actually loaded,
        not just when Page.navigate/Page.reload's own command response
        comes back. Times out rather than raising: an already-loaded
        page, a navigation that never fires 'load' (a download, a non-
        HTML response), or any other edge case shouldn't wedge the
        whole panel -- the caller's resync still runs after a timeout,
        just without the extra confidence the real event would give."""
        self._load_event = asyncio.Event()
        try:
            await asyncio.wait_for(self._load_event.wait(), timeout=timeout)
        except TimeoutError:
            pass
        finally:
            self._load_event = None

    async def _navigate_and_resync(
        self, command: str, params: dict[str, Any] | None = None
    ) -> None:
        """Shared by navigate() and reload(): stops the screencast,
        issues the given Page.navigate/Page.reload command, waits for
        the resulting Page.loadEventFired, and only then re-applies
        Emulation.setDeviceMetricsOverride and restarts the screencast.

        All three of those steps are load-bearing, confirmed one at a
        time via a standalone CDP script isolating this from the rest
        of the app -- a real Chromium/CDP quirk, not a hypothesis:
        Page.getLayoutMetrics and a one-shot Page.captureScreenshot
        both correctly reported the current viewport throughout, but
        the *screencast's* own frames repeatedly reverted to some
        smaller, unrelated size across a navigation -- not just once
        right after Page.navigate's own response (reapplying metrics
        immediately there still left a later frame reverted, arriving
        around when the page's real load event fires), but seemingly
        any time the render frame gets torn down and recreated
        mid-load. Only stopping the screencast *before* navigating,
        waiting for the real load signal, and reapplying metrics/
        restarting fresh after it landed produced frames that stayed
        correct with nothing reverting them afterward."""
        await self._stop_screencast_if_running()
        await self.send(command, params)
        await self._wait_for_load()
        await self._apply_device_metrics(
            self._viewport_width, self._viewport_height, self._viewport_scale
        )
        await self._start_screencast_if_previously_running()

    async def navigate(self, url: str) -> None:
        # Bare-domain convenience ("baidu.com" -> "https://baidu.com"),
        # same as typing into a real browser's address bar -- but only
        # when there's no scheme at all, so an already-schemed URL (any
        # "xyz:" prefix -- data:, about:, file:, chrome:, not just http(s))
        # passes through untouched instead of getting "https://" wrongly
        # glued onto its front.
        if "://" not in url and ":" not in url.split("/")[0]:
            url = f"https://{url}"
        await self._navigate_and_resync("Page.navigate", {"url": url})

    async def reload(self) -> None:
        await self._navigate_and_resync("Page.reload")

    async def _navigate_by_history_delta(self, delta: int) -> None:
        """CDP has no direct Page.goBack/goForward -- Page.
        getNavigationHistory returns the full entry list plus the
        current index, and Page.navigateToHistoryEntry jumps to a
        specific entry's own id from that list. Routed through
        _navigate_and_resync the same as navigate()/reload(): jumping
        history is still a real navigation, and the same screencast-
        desync class of bug applies equally (the self-healing frame-
        size check in _read_loop is the real backstop either way, but
        there's no reason to skip the best-effort quick correction the
        other two navigation paths already get)."""
        history = await self.send("Page.getNavigationHistory")
        entries = history["entries"]
        target_index = history["currentIndex"] + delta
        if not 0 <= target_index < len(entries):
            raise BrowserPanelError(
                "No page to go back to" if delta < 0 else "No page to go forward to"
            )
        await self._navigate_and_resync(
            "Page.navigateToHistoryEntry", {"entryId": entries[target_index]["id"]}
        )

    async def go_back(self) -> None:
        await self._navigate_by_history_delta(-1)

    async def go_forward(self) -> None:
        await self._navigate_by_history_delta(1)

    async def dispatch_mouse(
        self,
        kind: str,
        x: float,
        y: float,
        *,
        button: str = "left",
        delta_x: float = 0,
        delta_y: float = 0,
    ) -> None:
        params: dict[str, Any] = {"type": kind, "x": x, "y": y, "button": button, "clickCount": 1}
        if kind == "mouseWheel":
            params["deltaX"] = delta_x
            params["deltaY"] = delta_y
        await self.send("Input.dispatchMouseEvent", params)

    async def insert_text(self, text: str) -> None:
        """Types a whole string at once (filling a focused field) rather
        than simulating individual keydown/keyup pairs per character --
        good enough for a login form; not a substitute for real per-key
        events if a page specifically needs those (rare for plain text
        inputs)."""
        await self.send("Input.insertText", {"text": text})

    async def dispatch_key(self, kind: str, key: str) -> None:
        """A handful of named keys (Enter/Backspace/Tab/arrows) that
        insert_text can't express -- everything printable goes through
        insert_text instead, see its own docstring.

        Real bug, live-reported: sending a bare {"type": "keyDown", "key":
        key, "windowsVirtualKeyCode": 0} for these (the previous shape
        here) doesn't reliably trigger the page's own default action --
        Backspace visibly did nothing in a real text field. Chrome's
        default handling for a non-printable key depends on the actual
        Windows virtual-key code, not just the `key` string; hardcoding it
        to 0 left Chrome unable to tell *which* key this was supposed to
        be. Fixed the same way Puppeteer's own CDP keyboard implementation
        does it (confirmed by reading that implementation, not guessed):
        `type: "rawKeyDown"` for the down event (CDP's own "keyDown" is
        for a key that also carries printable `text`, which none of these
        do -- text goes through insert_text instead) plus the real
        `windowsVirtualKeyCode`/`code` for whichever key this is, from
        _KEY_DEFINITIONS below. An unrecognized key name falls back to the
        previous (best-effort, not reliable) behavior rather than
        silently dropping the event."""
        definition = _KEY_DEFINITIONS.get(key)
        params: dict[str, Any] = {
            "type": "rawKeyDown" if kind == "keyDown" else kind,
            "key": key,
            "windowsVirtualKeyCode": definition.vk_code if definition else 0,
        }
        if definition:
            params["code"] = definition.code
        await self.send("Input.dispatchKeyEvent", params)

    async def pick_element(self, x: float, y: float) -> dict[str, Any]:
        """Returns {"screenshot": <base64 jpeg, cropped to the element's
        own bounding box>, "text": <its innerText, truncated>, "tag": ...}
        for whatever's at (x, y) in the current viewport -- the panel's
        "select element" mode calls this to commit the element the user
        was just hovering (see hover_element above for the live-preview
        half of that same interaction).

        `clip.scale` is deliberately 1, not `self._viewport_scale` --
        tried the latter first (a plausible-looking fix for a live report
        of this specific screenshot still looking low-resolution even
        after the live view's own DPI fix landed) and then verified it
        live against a real Chromium instance before shipping it, which
        is exactly what caught this: `Page.captureScreenshot`'s
        `clip.scale` is a multiplier *on top of* whatever
        Emulation.setDeviceMetricsOverride's own deviceScaleFactor
        already applies, not a replacement for it -- confirmed by
        measuring a picked element's actual output pixel dimensions
        (naturalWidth/naturalHeight of the resulting <img>) at
        deviceScaleFactor=2 with `scale: 1` (correctly already 2x the
        element's CSS size -- DPI-aware) vs. `scale: self._viewport_scale`
        (4x, doubly-scaled, a real regression that would have shipped).
        So `1` here already means "match the emulated device's own
        scale," the same thing the fix was trying to achieve -- this
        docstring exists so a future well-intentioned read of this code
        doesn't reintroduce the same wrong "fix"."""
        value = await self._element_at(x, y)
        if value is None:
            raise BrowserPanelError("Nothing found at that position")
        rect = value["rect"]
        shot = await self.send(
            "Page.captureScreenshot",
            {
                "format": "jpeg",
                "clip": {
                    "x": rect["x"],
                    "y": rect["y"],
                    "width": rect["width"],
                    "height": rect["height"],
                    "scale": 1,
                },
                # The real bug behind a live report ("下方那个预览不全" --
                # the Add-to-chat preview is incomplete): the panel's own
                # emulated viewport is only as wide as the panel's own
                # on-screen CSS width (DEFAULT_PANEL_WIDTH = 440 in
                # BrowserPanel.tsx) -- narrower than plenty of real page
                # elements (a search bar, a results row). Without this
                # flag, Page.captureScreenshot only rasterizes whatever
                # Chrome actually composited within that viewport's real
                # bounds; a clip extending past the viewport's right edge
                # (or bottom) comes back correct up to that edge and
                # blank white beyond it -- not a decode/CSS-rendering bug
                # at the frontend at all, confirmed by reading the raw
                # captured JPEG bytes directly (correct dimensions,
                # correctly-formed file, content genuinely blank past the
                # viewport edge). captureBeyondViewport tells Chrome to
                # actually render/composite the requested clip region
                # even where it extends outside the current viewport.
                "captureBeyondViewport": True,
            },
        )
        return {"screenshot": shot["data"], "text": value["text"], "tag": value["tag"]}

    async def close(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001 -- best-effort on the way out
                pass
        if self._process is not None and self._process.returncode is None:
            self._process.kill()
            with_timeout = asyncio.wait_for(self._process.wait(), timeout=5.0)
            try:
                await with_timeout
            except (TimeoutError, ProcessLookupError):
                pass
        if self._profile_dir is not None:
            shutil.rmtree(self._profile_dir, ignore_errors=True)
