"""Tests for the Browser panel: web/browser_panel.py's own pure-Python
bits (find_browser_executable's env-override escape hatch, _free_port),
and the /ws/browser route in web/app.py.

The route tests monkeypatch coscribe.web.app.BrowserPanelSession with a
FakeBrowserPanelSession rather than launching a real browser -- that
class's own CDP-driving logic (launch/screencast/navigate/input/element
picking) was already verified against a real headless Chromium via a
standalone smoke test during development (see ROADMAP.md's Browser panel
entry); what these tests cover instead is the route's own message-routing
and error-handling contract, which a real-browser test can't isolate from
CDP timing.
"""

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langgraph", reason="needs the langgraph_spike extra installed")

from fastapi.testclient import TestClient

from coscribe.config import Settings
from coscribe.web.app import create_app_lg
from coscribe.web.browser_panel import (
    BrowserPanelError,
    BrowserPanelSession,
    find_browser_executable,
)


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "default_model": "fake:model",
        "workspace_root": tmp_path / "workspace",
        "state_dir": tmp_path / "state",
        "skills_dir": tmp_path / "skills",
        "memory_path": tmp_path / "MEMORY.md",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg, arg-type]


def test_find_browser_executable_honors_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_exe = tmp_path / "fake-chrome"
    fake_exe.write_text("")
    monkeypatch.setenv("COSCRIBE_BROWSER_PANEL_EXE", str(fake_exe))

    assert find_browser_executable() == str(fake_exe)


def test_find_browser_executable_ignores_override_pointing_nowhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COSCRIBE_BROWSER_PANEL_EXE", str(tmp_path / "nonexistent"))
    monkeypatch.setattr("sys.platform", "linux")

    assert find_browser_executable() is None


async def test_dispatch_key_sends_raw_key_down_with_real_vk_code_for_backspace() -> None:
    """Regression test for a real, live-reported bug: Backspace visibly
    did nothing in a real text field. The old code sent a bare
    {"type": "keyDown", "windowsVirtualKeyCode": 0} -- Chrome's default
    handling for a non-printable key depends on the real Windows
    virtual-key code, which 0 never identifies as anything."""
    session = BrowserPanelSession()
    calls: list[tuple[str, dict[str, object] | None]] = []

    async def fake_send(method: str, params: dict[str, object] | None = None) -> dict[str, object]:
        calls.append((method, params))
        return {}

    session.send = fake_send  # type: ignore[method-assign]

    await session.dispatch_key("keyDown", "Backspace")

    assert calls == [
        (
            "Input.dispatchKeyEvent",
            {
                "type": "rawKeyDown",
                "key": "Backspace",
                "windowsVirtualKeyCode": 0x08,
                "code": "Backspace",
            },
        )
    ]


async def test_dispatch_key_up_stays_keyup_not_raw() -> None:
    session = BrowserPanelSession()
    calls: list[tuple[str, dict[str, object] | None]] = []

    async def fake_send(method: str, params: dict[str, object] | None = None) -> dict[str, object]:
        calls.append((method, params))
        return {}

    session.send = fake_send  # type: ignore[method-assign]

    await session.dispatch_key("keyUp", "Backspace")

    assert calls == [
        (
            "Input.dispatchKeyEvent",
            {
                "type": "keyUp",
                "key": "Backspace",
                "windowsVirtualKeyCode": 0x08,
                "code": "Backspace",
            },
        )
    ]


async def test_dispatch_key_unrecognized_key_falls_back_to_best_effort() -> None:
    """A key not in _KEY_DEFINITIONS (nothing observed to send one today,
    but onCanvasKeyDown forwards whatever e.key is) still gets a valid,
    if imprecise, event instead of being silently dropped."""
    session = BrowserPanelSession()
    calls: list[tuple[str, dict[str, object] | None]] = []

    async def fake_send(method: str, params: dict[str, object] | None = None) -> dict[str, object]:
        calls.append((method, params))
        return {}

    session.send = fake_send  # type: ignore[method-assign]

    await session.dispatch_key("keyDown", "F5")

    assert calls == [
        ("Input.dispatchKeyEvent", {"type": "rawKeyDown", "key": "F5", "windowsVirtualKeyCode": 0})
    ]


async def test_pick_element_screenshot_clip_scale_stays_1_regardless_of_viewport_scale() -> None:
    """Guards against reintroducing a real near-miss: it looks tempting
    to set clip.scale to self._viewport_scale to make the picked-element
    screenshot DPI-aware, matching the live view's own DPI fix elsewhere
    -- tried exactly that, then verified live against a real Chromium
    instance before shipping it (measuring a picked element's actual
    output pixel dimensions), which caught that clip.scale is a
    multiplier *on top of* whatever deviceScaleFactor emulation already
    applies, not a replacement for it: at deviceScaleFactor=2, `scale: 1`
    already produces a correctly 2x-scaled (DPI-aware) image, while
    `scale: self._viewport_scale` (2) produced a 4x, doubly-scaled one.
    See pick_element's own docstring for the full writeup."""
    session = BrowserPanelSession()
    session._viewport_scale = 2.0  # type: ignore[attr-defined]

    async def fake_send(method: str, params: dict[str, object] | None = None) -> dict[str, object]:
        if method == "Runtime.evaluate":
            return {
                "result": {
                    "value": {
                        "tag": "button",
                        "text": "hello",
                        "rect": {"x": 1.0, "y": 2.0, "width": 3.0, "height": 4.0},
                    }
                }
            }
        assert method == "Page.captureScreenshot"
        assert params == {
            "format": "jpeg",
            "clip": {"x": 1.0, "y": 2.0, "width": 3.0, "height": 4.0, "scale": 1},
            "captureBeyondViewport": True,
        }
        return {"data": "cafebabe"}

    session.send = fake_send  # type: ignore[method-assign]

    result = await session.pick_element(1, 1)

    assert result == {"screenshot": "cafebabe", "text": "hello", "tag": "button"}


async def test_pick_element_captures_beyond_the_panels_own_narrow_viewport() -> None:
    """Regression test for a real, live-reported bug: "下方那个预览不全"
    (the Add-to-chat preview is incomplete) -- confirmed by capturing a
    real element wider than the panel's own emulated viewport
    (BrowserPanel.tsx's DEFAULT_PANEL_WIDTH is 440) against a real
    Chromium instance and inspecting the raw returned JPEG bytes
    directly: correctly formed, correct dimensions, but genuinely blank
    past the viewport's own right edge -- not a frontend CSS/decode bug.
    Page.captureScreenshot only rasterizes what Chrome actually
    composited within the current viewport's real bounds unless told to
    capture beyond it."""
    session = BrowserPanelSession()

    async def fake_send(method: str, params: dict[str, object] | None = None) -> dict[str, object]:
        if method == "Runtime.evaluate":
            return {
                "result": {
                    "value": {
                        "tag": "div",
                        "text": "a wide row",
                        "rect": {"x": 0.0, "y": 0.0, "width": 900.0, "height": 40.0},
                    }
                }
            }
        assert method == "Page.captureScreenshot"
        assert params is not None
        assert params["captureBeyondViewport"] is True
        return {"data": "cafebabe"}

    session.send = fake_send  # type: ignore[method-assign]

    await session.pick_element(1, 1)


async def test_resize_grows_the_real_window_when_too_small_for_the_requested_viewport() -> None:
    """Regression test for a real bug confirmed live before it could ship
    at all: BrowserPanel.tsx now asks resize() for the user's real screen
    resolution (remoteViewportSize), not this panel's own on-screen
    width -- launch()'s fixed --window-size=1920,1200 is no longer a safe
    assumed ceiling (plenty of ordinary monitors exceed it), and a
    too-small real window clips the screencast to its own bounds exactly
    the way launch()'s own comment already documented for the old,
    narrower case. Confirmed live in this sandbox with a real Chromium
    instance: a screencast requested at 2560x1440 against an unresized
    1920x1200 window came back clipped to 1920x1061."""
    session = BrowserPanelSession()
    calls: list[tuple[str, dict[str, object] | None]] = []

    async def fake_send(method: str, params: dict[str, object] | None = None) -> dict[str, object]:
        calls.append((method, params))
        if method == "Browser.getWindowForTarget":
            return {"windowId": 42, "bounds": {"width": 1920, "height": 1200}}
        return {}

    session.send = fake_send  # type: ignore[method-assign]

    await session.resize(2560, 1440, scale=1.0)

    assert ("Browser.getWindowForTarget", None) in calls
    assert (
        "Browser.setWindowBounds",
        {"windowId": 42, "bounds": {"width": 2760, "height": 1640}},
    ) in calls


async def test_resize_skips_growing_the_real_window_when_already_big_enough() -> None:
    session = BrowserPanelSession()
    calls: list[tuple[str, dict[str, object] | None]] = []

    async def fake_send(method: str, params: dict[str, object] | None = None) -> dict[str, object]:
        calls.append((method, params))
        if method == "Browser.getWindowForTarget":
            return {"windowId": 42, "bounds": {"width": 3000, "height": 3000}}
        return {}

    session.send = fake_send  # type: ignore[method-assign]

    await session.resize(800, 600, scale=1.0)

    assert ("Browser.getWindowForTarget", None) in calls
    assert not any(method == "Browser.setWindowBounds" for method, _ in calls)


class FakeBrowserPanelSession:
    """Stands in for the real BrowserPanelSession -- records every call
    the /ws/browser route makes so tests can assert on the route's own
    dispatch logic without spawning a real browser process."""

    launch_error: str | None = None

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.on_frame: Any = None
        self.closed = False

    async def launch(self) -> None:
        if self.launch_error is not None:
            raise BrowserPanelError(self.launch_error)

    async def start_screencast(self, on_frame: Any) -> None:
        self.on_frame = on_frame
        # Emits one frame immediately, same as a real screencast would the
        # moment Page.startScreencast takes effect -- lets tests assert the
        # frame reaches the client without needing a second round trip.
        await on_frame("deadbeef")

    async def navigate(self, url: str) -> None:
        self.calls.append(("navigate", url))

    async def reload(self) -> None:
        self.calls.append(("reload", None))

    async def go_back(self) -> None:
        self.calls.append(("back", None))

    async def go_forward(self) -> None:
        self.calls.append(("forward", None))

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
        self.calls.append(("mouse", (kind, x, y, button, delta_x, delta_y)))

    async def dispatch_key(self, kind: str, key: str) -> None:
        self.calls.append(("key", (kind, key)))

    async def insert_text(self, text: str) -> None:
        self.calls.append(("text", text))

    async def pick_element(self, x: float, y: float) -> dict[str, Any]:
        self.calls.append(("pick_element", (x, y)))
        return {"screenshot": "cafebabe", "text": "hello", "tag": "div"}

    async def resize(self, width: int, height: int, scale: float = 1.0) -> None:
        self.calls.append(("resize", (width, height, scale)))

    async def hover_element(self, x: float, y: float) -> dict[str, Any] | None:
        self.calls.append(("hover_element", (x, y)))
        return {"tag": "span", "rect": {"x": 1.0, "y": 2.0, "width": 3.0, "height": 4.0}}

    async def close(self) -> None:
        self.closed = True


class FailingPickBrowserPanelSession(FakeBrowserPanelSession):
    async def pick_element(self, x: float, y: float) -> dict[str, Any]:
        raise BrowserPanelError("nothing under that point")


def _client(tmp_path: Path) -> TestClient:
    app = create_app_lg(_settings(tmp_path))
    return TestClient(app)


def test_ws_browser_reports_launch_failure_and_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeBrowserPanelSession.launch_error = "no browser found"
    monkeypatch.setattr("coscribe.web.app.BrowserPanelSession", FakeBrowserPanelSession)

    with _client(tmp_path) as client, client.websocket_connect("/ws/browser") as ws:
        message = ws.receive_json()
        assert message == {"type": "error", "message": "no browser found"}


def test_ws_browser_forwards_screencast_frame_on_connect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeBrowserPanelSession.launch_error = None
    monkeypatch.setattr("coscribe.web.app.BrowserPanelSession", FakeBrowserPanelSession)

    with _client(tmp_path) as client, client.websocket_connect("/ws/browser") as ws:
        message = ws.receive_json()
        assert message == {"type": "frame", "data": "deadbeef"}


def test_ws_browser_routes_navigate_reload_input_and_pick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeBrowserPanelSession.launch_error = None
    sessions: list[FakeBrowserPanelSession] = []
    real_init = FakeBrowserPanelSession.__init__

    def _tracking_init(self: FakeBrowserPanelSession) -> None:
        real_init(self)
        sessions.append(self)

    monkeypatch.setattr(FakeBrowserPanelSession, "__init__", _tracking_init)
    monkeypatch.setattr("coscribe.web.app.BrowserPanelSession", FakeBrowserPanelSession)

    with _client(tmp_path) as client, client.websocket_connect("/ws/browser") as ws:
        ws.receive_json()  # initial frame from start_screencast

        ws.send_json({"type": "navigate", "url": "example.com"})
        ws.send_json({"type": "reload"})
        ws.send_json({"type": "back"})
        ws.send_json({"type": "forward"})
        ws.send_json({"type": "mouse", "kind": "mousePressed", "x": 1, "y": 2})
        ws.send_json({"type": "key", "kind": "keyDown", "key": "Enter"})
        ws.send_json({"type": "text", "text": "hi"})
        ws.send_json({"type": "resize", "width": 500, "height": 700, "scale": 2.0})
        ws.send_json({"type": "hover_element", "x": 5, "y": 6})
        ws.send_json({"type": "pick_element", "x": 3, "y": 4})

        hovered = ws.receive_json()
        assert hovered == {
            "type": "hover",
            "element": {"tag": "span", "rect": {"x": 1.0, "y": 2.0, "width": 3.0, "height": 4.0}},
        }
        picked = ws.receive_json()
        assert picked == {"type": "picked", "screenshot": "cafebabe", "text": "hello", "tag": "div"}

    session = sessions[0]
    assert session.calls == [
        ("navigate", "example.com"),
        ("reload", None),
        ("back", None),
        ("forward", None),
        ("mouse", ("mousePressed", 1, 2, "left", 0, 0)),
        ("key", ("keyDown", "Enter")),
        ("text", "hi"),
        ("resize", (500, 700, 2.0)),
        ("hover_element", (5, 6)),
        ("pick_element", (3, 4)),
    ]
    assert session.closed is True  # the `with` block above already closed the socket


def test_ws_browser_reports_mid_session_error_without_closing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    FailingPickBrowserPanelSession.launch_error = None
    monkeypatch.setattr("coscribe.web.app.BrowserPanelSession", FailingPickBrowserPanelSession)

    with _client(tmp_path) as client, client.websocket_connect("/ws/browser") as ws:
        ws.receive_json()  # initial frame

        ws.send_json({"type": "pick_element", "x": 1, "y": 1})
        error = ws.receive_json()
        assert error == {"type": "error", "message": "nothing under that point"}

        # The socket is still alive after a mid-session BrowserPanelError --
        # only launch() failures close the connection (see the route's own
        # try/except around session.launch()).
        ws.send_json({"type": "reload"})
