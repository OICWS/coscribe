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
import base64
import itertools
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..runtime.types import tool_metadata
from ._workspace import WorkspaceScope

_DEFAULT_TIMEOUT = 30.0
_NAVIGATE_TIMEOUT = 60.0
# The desktop app may first ask the user whether the AI can use the site
# (it gives up after three minutes), so every call waits that much longer.
_PERMISSION_WAIT = 190.0
# How long a step waits for its element, text or download by default, and
# at most -- a saved workflow may wait out a slow export.
_DEFAULT_WAIT = 30.0
_MAX_WAIT = 3600.0
_DOWNLOADS_FOLDER = "downloads"
_SNAPSHOT_CHARS = 12_000
_EVALUATE_CHARS = 5_000

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
        self._threads: dict[str, str] = {}
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
        self._threads.clear()
        for future in pending.values():
            if not future.done():
                future.set_result({"ok": False, "error": message})

    def set_active(self, thread_id: str, active: bool) -> None:
        """Tell the desktop app a conversation's turn or a workflow run has
        started or ended: a download from its tab in between is saved
        without asking, even one that begins long after the step that set
        it off. Call on the server's loop."""
        if self._outbox is not None:
            self._outbox.put_nowait(
                {"id": "", "thread_id": thread_id, "action": "activity", "args": {"active": active}}
            )

    def cancel(self, thread_id: str) -> None:
        """Stop the conversation's browser step: the desktop app drops it
        (a wait there can run for an hour) and whatever it queued, and the
        tool call waiting on it returns now. Call on the server's loop."""
        if self._outbox is not None:
            self._outbox.put_nowait(
                {"id": "", "thread_id": thread_id, "action": "cancel", "args": {}}
            )
        for request_id in [r for r, t in self._threads.items() if t == thread_id]:
            future = self._pending.pop(request_id, None)
            self._threads.pop(request_id, None)
            if future is not None and not future.done():
                future.set_result({"ok": False, "error": "Stopped by the user."})

    async def request(
        self, action: str, args: dict[str, Any], thread_id: str, timeout: float
    ) -> dict[str, Any]:
        if self._outbox is None:
            raise BrowserUnavailableError(_UNAVAILABLE)
        request_id = str(next(self._ids))
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        self._threads[request_id] = thread_id
        await self._outbox.put(
            {"id": request_id, "thread_id": thread_id, "action": action, "args": args}
        )
        try:
            reply = await asyncio.wait_for(future, timeout)
        except TimeoutError as exc:
            raise ValueError(f"The browser didn't finish {action} within {timeout:.0f}s.") from exc
        finally:
            self._pending.pop(request_id, None)
            self._threads.pop(request_id, None)
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

# Workflow runs and test runs that use the browser take turns: they share
# its tabs, and two at once would navigate them out from under each other.
BROWSER_RUNS = asyncio.Lock()


def _tab_line(tab: Any) -> str:
    if not isinstance(tab, dict):
        return ""
    title = str(tab.get("title") or "").strip() or "(untitled)"
    return f"Tab {tab.get('id')}: {title} -- {tab.get('url') or 'about:blank'}"


def _with_tab(message: str, result: dict[str, Any]) -> str:
    """The step's outcome, then what the page now holds open for the model
    to deal with, then the tab it happened in."""
    lines = [message]
    if result.get("dialog"):
        lines.append(str(result["dialog"]))
    if result.get("file_chooser"):
        lines.append(
            "That opened a file chooser -- choose the files with browser_file_upload(paths=[...])."
        )
    for download in result.get("downloads") or []:
        path, state = download.get("path"), download.get("state")
        if state == "completed":
            lines.append(f"Downloaded {path}")
        elif state == "progressing":
            lines.append(
                f"Downloading {path} -- browser_wait_for(download=True) waits until it's done."
            )
        else:
            lines.append(f"Download of {path}: {state}")
    line = _tab_line(result.get("tab"))
    if line:
        lines.append(line)
    return "\n".join(lines)


def _free_path(folder: Path, name: str) -> Path:
    """`name` in `folder`, as "name (1).ext" and so on if taken -- the way
    the browser names a download that would overwrite a file."""
    path = folder / name
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 1
    while path.exists():
        path = folder / f"{stem} ({n}){suffix}"
        n += 1
    return path


SCREENSHOT_FOLDER = "screenshots"


def page_screenshot(
    thread_id: str,
    state_dir: Path,
    host: BrowserHost = BROWSER_HOST,
    *,
    saved_workflow: bool = False,
) -> str | None:
    """The conversation's current page as a PNG under the state folder:
    its file name, or None when there's no page to capture. Blocks; call
    it off the server's loop."""
    args: dict[str, Any] = {"saved_workflow": True} if saved_workflow else {}
    try:
        result = host.call("screenshot", args, thread_id, _DEFAULT_TIMEOUT)
    except Exception:  # noqa: BLE001 -- evidence is optional; the run's own result stands
        return None
    png = result.get("png")
    if not isinstance(png, str):
        return None
    folder = state_dir / SCREENSHOT_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.png"
    (folder / name).write_bytes(base64.b64decode(png))
    return name


def build_browser_tools(
    thread_id: str,
    host: BrowserHost = BROWSER_HOST,
    *,
    scope: WorkspaceScope | None = None,
    saved_workflow: bool = False,
) -> list[Callable[..., Any]]:
    """`saved_workflow`: these tools run a saved workflow's steps, which
    the user reviewed and saved -- consent to the sites they visit -- so
    the desktop app doesn't stop an unattended run to ask."""

    def call(action: str, limit: float = _DEFAULT_TIMEOUT, **args: Any) -> dict[str, Any]:
        if scope is not None:
            args["download_dir"] = str(scope.root / _DOWNLOADS_FOLDER)
        if saved_workflow:
            args["saved_workflow"] = True
        result = host.call(action, args, thread_id, limit + _PERMISSION_WAIT)
        if scope is not None:
            # As the file tools take them: relative to the workspace.
            for download in result.get("downloads") or []:
                path = Path(str(download.get("path")))
                download["path"] = scope.relative(path) if path.is_absolute() else str(path)
        return result

    def wait_limit(timeout: float) -> float:
        if timeout <= 0:
            raise ValueError("timeout must be more than 0 seconds.")
        return min(float(timeout), _MAX_WAIT)

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
        snapshot may no longer exist. In place of a ref those tools also
        take the element as the snapshot prints it, e.g. 'checkbox "All
        items"' (add " #2" for the second such): it waits for the element
        to appear (for the action's timeout), and it's what a saved
        workflow must use, since refs are numbered afresh on every load.

        Args:
            start: character offset to continue a long page from (the
                previous snapshot says where it stopped)
        """
        result = call("snapshot", start=max(0, start), max_chars=_SNAPSHOT_CHARS)
        text = str(result.get("text") or "")
        total = int(result.get("total_chars") or len(text))
        end = max(0, start) + len(text)
        header = _with_tab("", result).strip()
        if end < total:
            text += (
                f"\n\n[Page continues: {end} of {total} characters shown. "
                f"Call browser_snapshot(start={end}) for more.]"
            )
        return f"{header}\n\n{text}" if header else text

    def browser_click(ref: str, double: bool = False, timeout: float = _DEFAULT_WAIT) -> str:
        """Click an element in coscribe's browser, by its ref from the latest
        browser_snapshot. Clicks as a real mouse would, so links open and
        buttons submit.

        Args:
            ref: the element's ref, e.g. "e12", or its description
            double: double-click instead of a single click
            timeout: how long a described element may take to appear, in
                seconds (default 30, up to 3600)
        """
        wait = wait_limit(timeout)
        result = call("click", wait + _DEFAULT_TIMEOUT, ref=ref, double=double, timeout=wait)
        return _with_tab(f"Clicked {result.get('element') or ref}.", result)

    def browser_type(
        ref: str, text: str, submit: bool = False, timeout: float = _DEFAULT_WAIT
    ) -> str:
        """Type into a text field in coscribe's browser, replacing what's in
        it, by the field's ref from the latest browser_snapshot.

        Args:
            ref: the field's ref, e.g. "e7", or its description
            text: what to type
            submit: press Enter afterwards (e.g. to run a search)
            timeout: how long a described element may take to appear, in
                seconds (default 30, up to 3600)
        """
        wait = wait_limit(timeout)
        result = call(
            "type", wait + _DEFAULT_TIMEOUT, ref=ref, text=text, submit=submit, timeout=wait
        )
        return _with_tab(f"Typed into {result.get('element') or ref}.", result)

    def browser_press_key(key: str) -> str:
        """Press a key in coscribe's browser, on whatever has focus -- e.g.
        "Enter", "Escape", "Tab", "ArrowDown", "PageDown", "Backspace",
        "F1".."F12", or a combination like "Control+a" or "Shift+F4".

        Args:
            key: the key or combination
        """
        return _with_tab(f"Pressed {key}.", call("press_key", key=key))

    def browser_select_option(ref: str, values: list[str], timeout: float = _DEFAULT_WAIT) -> str:
        """Choose option(s) in a dropdown (<select>) in coscribe's browser.

        Args:
            ref: the dropdown's ref from the latest browser_snapshot, or
                its description
            values: the options to choose, by their visible text or value
            timeout: how long a described element may take to appear, in
                seconds (default 30, up to 3600)
        """
        wait = wait_limit(timeout)
        result = call(
            "select_option", wait + _DEFAULT_TIMEOUT, ref=ref, values=values, timeout=wait
        )
        chosen = ", ".join(str(v) for v in result.get("selected") or values)
        return _with_tab(f"Selected {chosen}.", result)

    def browser_hover(ref: str, timeout: float = _DEFAULT_WAIT) -> str:
        """Move the mouse over an element in coscribe's browser, e.g. to open
        a menu that appears on hover.

        Args:
            ref: the element's ref from the latest browser_snapshot, or its
                description
            timeout: how long a described element may take to appear, in
                seconds (default 30, up to 3600)
        """
        wait = wait_limit(timeout)
        result = call("hover", wait + _DEFAULT_TIMEOUT, ref=ref, timeout=wait)
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

    def browser_wait_for(
        text: str = "",
        gone: str = "",
        element: str = "",
        download: bool = False,
        seconds: float = 0,
        timeout: float = _DEFAULT_WAIT,
        save_to: str = "",
    ) -> str | dict[str, Any]:
        """Wait in coscribe's browser for one thing: text to appear, text to
        go away (e.g. "Loading…"), an element to appear, or a download to
        finish -- or just for some seconds. Use it for anything slow, like
        a report or export that takes minutes.

        Args:
            text: wait until this text is on the page
            gone: wait until this text is no longer on the page
            element: wait until this element is there, as the snapshot
                prints it, e.g. 'button "Download"'
            download: wait until the page's download (started earlier or
                while waiting) has finished; the result's "file" is where
                it was saved -- the name changes from run to run
            seconds: just wait this long
            timeout: give up after this many seconds (default 30, up to 3600)
            save_to: with download, a folder to move the file into (the
                workspace's downloads/ otherwise); one you can write to
        """
        if save_to and not download:
            raise ValueError("save_to goes with download=True.")
        folder = None
        if save_to:
            if scope is None:
                raise ValueError("Saving downloads elsewhere isn't available in this conversation.")
            # Before waiting: a folder outside the allowed ones should fail
            # now, not after a twenty-minute export.
            folder = scope.resolve(save_to, write=True)
        chosen = [n for n, v in (("text", text), ("gone", gone), ("element", element)) if v]
        if download:
            chosen.append("download")
        if len(chosen) > 1:
            raise ValueError(f"Wait for one thing at a time, not {' and '.join(chosen)}.")
        limit = wait_limit(max(timeout, seconds))
        if not chosen:
            if seconds <= 0:
                raise ValueError("Pass text, gone, element or download=True -- or seconds.")
            result = call("wait_for", seconds + 10, seconds=seconds, timeout=limit)
            return _with_tab(f"Waited {seconds:g}s.", result)
        result = call(
            "wait_for",
            limit + 15,
            text=text or None,
            gone=gone or None,
            element=element or None,
            download=download,
            timeout=limit,
        )
        if download:
            # A saved workflow's next step reads the file from here: the
            # name a site gives an export changes run to run.
            done = [d for d in result.get("downloads") or [] if d.get("state") == "completed"]
            if folder is not None and scope is not None:
                folder.mkdir(parents=True, exist_ok=True)
                for item in done:
                    source = scope.resolve(str(item.get("path")))
                    target = _free_path(folder, str(item.get("name") or source.name))
                    shutil.move(str(source), target)
                    item["path"] = scope.relative(target)
            files = [str(item.get("path")) for item in done]
            return {
                "file": files[-1] if files else "",
                "files": files,
                "message": _with_tab("The download finished.", result),
            }
        if element:
            message = f"{element} is on the page."
        elif gone:
            message = f'"{gone}" is gone from the page.'
        else:
            message = f'"{text}" is on the page.'
        return _with_tab(message, result)

    def browser_tabs(action: str = "list", tab_id: int = 0, url: str = "") -> str:
        """List, open, switch to or close tabs in coscribe's browser (at
        most 9 are open at once). The other browser_* tools act on the
        current tab.

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

    def browser_handle_dialog(accept: bool = True) -> str:
        """Answer the alert or confirm dialog open on the current page in
        coscribe's browser (a step's result says when one opened; the page
        waits until it's answered).

        Args:
            accept: OK (True) or Cancel (False)
        """
        result = call("handle_dialog", accept=accept)
        kind = str(result.get("dialog_type") or "dialog")
        answer = "Accepted" if result.get("accepted", accept) else "Dismissed"
        return _with_tab(f'{answer} the {kind}: "{result.get("message") or ""}".', result)

    def browser_file_upload(paths: list[str], ref: str = "") -> str:
        """Upload files from the user's folders to the page in coscribe's
        browser, as if the user had picked them. Pass the ref of the upload
        button or file field; leave ref empty to answer a file chooser a
        click just opened.

        Args:
            paths: the files, relative to the workspace or absolute under a
                folder you can read
            ref: the upload button or file field from browser_snapshot
        """
        if not paths:
            raise ValueError("Pass at least one file in paths.")
        if scope is None:
            raise ValueError("Uploading files isn't available in this conversation.")
        files = []
        for path in paths:
            resolved = scope.resolve(path)
            if not resolved.is_file():
                raise ValueError(f"No such file: {path}")
            files.append(str(resolved))
        result = call("upload", ref=ref or None, paths=files)
        names = ", ".join(scope.relative(Path(f)) for f in files)
        return _with_tab(f"Uploaded {names} to {result.get('element') or 'the page'}.", result)

    def browser_evaluate(expression: str) -> str:
        """Run a JavaScript expression in the current page of coscribe's
        browser, as the page's own scripts would, and return its value
        (awaited if it's a promise). For reading what a snapshot doesn't
        show or doing what clicks can't; prefer the other browser_* tools
        when they can do the job.

        Args:
            expression: e.g. "document.title" or
                "[...document.querySelectorAll('h2')].map(h => h.textContent)"
        """
        if not expression.strip():
            raise ValueError("Pass a JavaScript expression.")
        result = call("evaluate", expression=expression)
        value = str(result.get("value") if result.get("value") is not None else "null")
        if len(value) > _EVALUATE_CHARS:
            value = (
                f"{value[:_EVALUATE_CHARS]}\n[... {len(value) - _EVALUATE_CHARS} more "
                "characters -- return less, e.g. a slice or a count]"
            )
        return _with_tab(value, result)

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
        browser_handle_dialog,
        browser_file_upload,
    ]
    return (
        [tool_metadata(f, risk_category="READ", category="browser") for f in read]
        + [tool_metadata(f, risk_category="EXTERNAL", category="browser") for f in act]
        # Arbitrary script with the user's logins behind it.
        + [tool_metadata(browser_evaluate, risk_category="EXEC", category="browser")]
    )
