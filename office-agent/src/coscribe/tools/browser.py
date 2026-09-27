"""The browser_* tools: they drive the tabs in the desktop app's Browser
panel, the same tabs the user sees, so every step happens in front of them
and uses their logins there.

The desktop app (office-agent-desktop, src/main/browserHost.ts) connects to
this server's /internal/browser-host stream with the token it started the
server with, receives one command at a time and posts each result back.
Commands travel over that connection instead of a remote-debugging port:
a debugging port would let any local process take over those logged-in
pages.

Tool functions run in LangGraph's worker threads, so each call hops onto
the server's event loop (where the connection lives) and blocks on the
reply.
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Callable
from typing import Any

from ..runtime.types import tool_metadata

_DEFAULT_TIMEOUT = 30.0
_NAVIGATE_TIMEOUT = 60.0
_MAX_WAIT_SECONDS = 30.0
_SNAPSHOT_CHARS = 12_000

_UNAVAILABLE = (
    "coscribe's browser isn't reachable right now -- it runs inside the coscribe "
    "desktop app, which has to be open. Tell the user, or use read_web_page for a "
    "page that doesn't need a login or clicking."
)


class BrowserUnavailableError(ValueError):
    pass


class BrowserHost:
    """The one desktop app connection. A reconnect replaces the previous
    connection, and whatever that one still owed a reply fails, rather
    than waiting on a stream nobody reads any more."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._outbox: asyncio.Queue[dict[str, Any]] | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._ids = itertools.count(1)

    @property
    def connected(self) -> bool:
        return self._outbox is not None

    def attach(self) -> asyncio.Queue[dict[str, Any]]:
        self._fail_pending("The desktop app reconnected; try the step again.")
        self._loop = asyncio.get_running_loop()
        self._outbox = asyncio.Queue()
        return self._outbox

    def detach(self, outbox: asyncio.Queue[dict[str, Any]]) -> None:
        if self._outbox is outbox:
            self._outbox = None
            self._fail_pending(_UNAVAILABLE)

    def resolve(self, request_id: str, reply: dict[str, Any]) -> bool:
        future = self._pending.pop(request_id, None)
        if future is None or future.done():
            return False
        future.set_result(reply)
        return True

    def _fail_pending(self, message: str) -> None:
        pending, self._pending = self._pending, {}
        for future in pending.values():
            if not future.done():
                future.set_result({"ok": False, "error": message})

    async def request(
        self, action: str, args: dict[str, Any], thread_id: str, timeout: float
    ) -> dict[str, Any]:
        if self._outbox is None:
            raise BrowserUnavailableError(_UNAVAILABLE)
        request_id = str(next(self._ids))
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._outbox.put(
            {"id": request_id, "thread_id": thread_id, "action": action, "args": args}
        )
        try:
            reply = await asyncio.wait_for(future, timeout)
        except TimeoutError as exc:
            raise ValueError(f"The browser didn't finish {action} within {timeout:.0f}s.") from exc
        finally:
            self._pending.pop(request_id, None)
        if not reply.get("ok"):
            raise ValueError(str(reply.get("error") or f"The browser couldn't {action}."))
        result = reply.get("result")
        return result if isinstance(result, dict) else {}

    def call(
        self,
        action: str,
        args: dict[str, Any],
        thread_id: str,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> dict[str, Any]:
        loop = self._loop
        if loop is None or self._outbox is None:
            raise BrowserUnavailableError(_UNAVAILABLE)
        future = asyncio.run_coroutine_threadsafe(
            self.request(action, args, thread_id, timeout), loop
        )
        return future.result(timeout + 5)


BROWSER_HOST = BrowserHost()


def _tab_line(tab: Any) -> str:
    if not isinstance(tab, dict):
        return ""
    title = str(tab.get("title") or "").strip() or "(untitled)"
    return f"Tab {tab.get('id')}: {title} -- {tab.get('url') or 'about:blank'}"


def _with_tab(message: str, result: dict[str, Any]) -> str:
    line = _tab_line(result.get("tab"))
    return f"{message}\n{line}" if line else message


def build_browser_tools(
    thread_id: str, host: BrowserHost = BROWSER_HOST
) -> list[Callable[..., Any]]:
    def call(action: str, timeout: float = _DEFAULT_TIMEOUT, **args: Any) -> dict[str, Any]:
        return host.call(action, args, thread_id, timeout)

    def browser_navigate(url: str) -> str:
        """Open a URL in coscribe's browser (the Browser panel the user
        watches), in the current tab. Use this instead of read_web_page when
        the page needs the user's login, JavaScript, clicking or typing.
        Then call browser_snapshot to see what's on the page.

        Args:
            url: the address; a bare domain like "example.com" works too
        """
        result = call("navigate", _NAVIGATE_TIMEOUT, url=url)
        return _with_tab("Loaded.", result)

    def browser_navigate_back() -> str:
        """Go back one page in the current tab of coscribe's browser."""
        return _with_tab("Went back.", call("back"))

    def browser_snapshot(start: int = 0) -> str:
        """Read the current tab of coscribe's browser: the page's headings,
        text, links, buttons and form fields in page order. Every element
        you can act on has a ref like [ref=e12] -- pass it to browser_click,
        browser_type, browser_select_option or browser_hover. Take a new
        snapshot after anything that changes the page; refs from an older
        snapshot may no longer exist.

        Args:
            start: character offset to continue a long page from (the
                previous snapshot says where it stopped)
        """
        result = call("snapshot", start=max(0, start), max_chars=_SNAPSHOT_CHARS)
        text = str(result.get("text") or "")
        total = int(result.get("total_chars") or len(text))
        end = max(0, start) + len(text)
        header = _tab_line(result.get("tab"))
        if end < total:
            text += (
                f"\n\n[Page continues: {end} of {total} characters shown. "
                f"Call browser_snapshot(start={end}) for more.]"
            )
        return f"{header}\n\n{text}" if header else text

    def browser_click(ref: str, double: bool = False) -> str:
        """Click an element in coscribe's browser, by its ref from the latest
        browser_snapshot. Clicks as a real mouse would, so links open and
        buttons submit.

        Args:
            ref: the element's ref, e.g. "e12"
            double: double-click instead of a single click
        """
        result = call("click", ref=ref, double=double)
        return _with_tab(f"Clicked {result.get('element') or ref}.", result)

    def browser_type(ref: str, text: str, submit: bool = False) -> str:
        """Type into a text field in coscribe's browser, replacing what's in
        it, by the field's ref from the latest browser_snapshot.

        Args:
            ref: the field's ref, e.g. "e7"
            text: what to type
            submit: press Enter afterwards (e.g. to run a search)
        """
        result = call("type", ref=ref, text=text, submit=submit)
        return _with_tab(f"Typed into {result.get('element') or ref}.", result)

    def browser_press_key(key: str) -> str:
        """Press a key in coscribe's browser, on whatever has focus -- e.g.
        "Enter", "Escape", "Tab", "ArrowDown", "PageDown", "Backspace", or a
        combination like "Control+a".

        Args:
            key: the key or combination
        """
        return _with_tab(f"Pressed {key}.", call("press_key", key=key))

    def browser_select_option(ref: str, values: list[str]) -> str:
        """Choose option(s) in a dropdown (<select>) in coscribe's browser.

        Args:
            ref: the dropdown's ref from the latest browser_snapshot
            values: the options to choose, by their visible text or value
        """
        result = call("select_option", ref=ref, values=values)
        chosen = ", ".join(str(v) for v in result.get("selected") or values)
        return _with_tab(f"Selected {chosen}.", result)

    def browser_hover(ref: str) -> str:
        """Move the mouse over an element in coscribe's browser, e.g. to open
        a menu that appears on hover.

        Args:
            ref: the element's ref from the latest browser_snapshot
        """
        result = call("hover", ref=ref)
        return _with_tab(f"Hovering over {result.get('element') or ref}.", result)

    def browser_scroll(direction: str = "down", ref: str = "") -> str:
        """Scroll the current page in coscribe's browser by about one
        screen, or bring one element into view.

        Args:
            direction: "down" or "up"
            ref: scroll this element into view instead (from browser_snapshot)
        """
        if direction not in ("down", "up"):
            raise ValueError('direction must be "down" or "up".')
        result = call("scroll", direction=direction, ref=ref or None)
        return _with_tab(str(result.get("note") or "Scrolled."), result)

    def browser_wait_for(text: str = "", seconds: float = 0) -> str:
        """Wait in coscribe's browser until some text appears on the page, or
        for a number of seconds (up to 30) -- for pages that load results
        after a moment.

        Args:
            text: wait until this text is on the page
            seconds: or just wait this long
        """
        if not text and seconds <= 0:
            raise ValueError("Pass text to wait for, or seconds to wait.")
        wait = min(float(seconds) if seconds > 0 else _MAX_WAIT_SECONDS, _MAX_WAIT_SECONDS)
        result = call("wait_for", wait + 10, text=text or None, seconds=wait)
        if text and not result.get("found"):
            raise ValueError(f'"{text}" didn\'t appear within {wait:.0f}s.')
        return _with_tab(f'"{text}" is on the page.' if text else f"Waited {wait:.0f}s.", result)

    def browser_tabs(action: str = "list", tab_id: int = 0, url: str = "") -> str:
        """List, open, switch to or close tabs in coscribe's browser. The
        other browser_* tools act on the current tab.

        Args:
            action: "list", "new" (opens url, or a blank tab), "select" or "close"
            tab_id: the tab for "select"/"close", from "list"
            url: for "new"
        """
        if action not in ("list", "new", "select", "close"):
            raise ValueError('action must be "list", "new", "select" or "close".')
        if action in ("select", "close") and not tab_id:
            raise ValueError(f'"{action}" needs a tab_id from browser_tabs("list").')
        timeout = _NAVIGATE_TIMEOUT if action == "new" and url else _DEFAULT_TIMEOUT
        result = call("tabs", timeout, op=action, tab_id=tab_id or None, url=url or None)
        tabs = result.get("tabs") or []
        lines = [
            f"{'* ' if isinstance(t, dict) and t.get('active') else '  '}{_tab_line(t)}"
            for t in tabs
        ]
        return "\n".join(lines) if lines else "No tabs are open."

    read: list[Callable[..., Any]] = [
        browser_navigate,
        browser_navigate_back,
        browser_snapshot,
        browser_hover,
        browser_scroll,
        browser_wait_for,
        browser_tabs,
    ]
    act: list[Callable[..., Any]] = [
        browser_click,
        browser_type,
        browser_press_key,
        browser_select_option,
    ]
    return [tool_metadata(f, risk_category="READ", category="browser") for f in read] + [
        tool_metadata(f, risk_category="EXTERNAL", category="browser") for f in act
    ]
