"""Test sites whose layout can change between runs: a form of a few pages, drawn from a
description, with "mutations" that redesign it the ways real sites change.

A site keeps what it was sent in memory (`Site.records`): what a workflow really filed is read
from there, not from what the run says it did.
"""

from __future__ import annotations

import html
import json
import threading
import urllib.parse
from dataclasses import dataclass, field
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MUTATIONS = {
    "relabel": "every label and button reworded",
    "overlay": "a cookie dialog covers the page until it is accepted",
    "required_extra": "a new required select whose value the data has in a column the task ignored",
    "required_missing": "a new required select the data has no value for: the task must stop",
    "review_page": "a page to check the entries before they are sent",
    "ref_format": "the reference changes format and loses its id",
    "date_format": "dates are typed as DD/MM/YYYY",
    "decoy": "the old final button now only saves a draft; another button files",
    "slow": "each form appears two seconds after the page loads",
    "reorder": "the fields of each page come in reverse order",
}


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    relabel: str
    kind: str = "text"  # text, email, number, date, select, radio, checkbox, textarea
    options: tuple[str, ...] = ()
    column: str = ""  # the data column the value comes from ("" for a checkbox the task ticks)


@dataclass(frozen=True)
class SiteSpec:
    slug: str
    title: str
    pages: tuple[tuple[Field, ...], ...]
    next_label: tuple[str, str]
    submit_label: tuple[str, str]
    done_heading: str
    ref_prefix: tuple[str, str]
    extra: Field  # the field required_extra / required_missing add to the first page
    check: Any = None  # (values) -> list of error messages, beyond "required"


@dataclass
class Site:
    spec: SiteSpec
    mutations: frozenset[str] = frozenset()
    records: list[dict[str, Any]] = field(default_factory=list)
    _sessions: dict[str, dict[str, str]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def has(self, mutation: str) -> bool:
        return mutation in self.mutations

    def reset(self, mutations: frozenset[str] = frozenset()) -> None:
        unknown = set(mutations) - set(MUTATIONS)
        if unknown:
            raise ValueError(f"unknown mutations: {sorted(unknown)}")
        with self._lock:
            self.mutations = frozenset(mutations)
            self.records.clear()
            self._sessions.clear()

    # -- what a page shows --------------------------------------------------------------------

    def label(self, f: Field) -> str:
        text = f.relabel if self.has("relabel") else f.label
        if f.kind == "date" and self.has("date_format"):
            text += " (DD/MM/YYYY)"
        return text

    def fields(self, page: int) -> list[Field]:
        fields = list(self.spec.pages[page])
        if page == 0 and (self.has("required_extra") or self.has("required_missing")):
            fields.append(self.spec.extra)
        return fields[::-1] if self.has("reorder") else fields

    def _input(self, f: Field, value: str) -> str:
        id_ = f"f-{f.name}"
        v = html.escape(value)
        if f.kind == "select":
            opts = "".join(
                f"<option{' selected' if value == o else ''}>{html.escape(o)}</option>"
                for o in f.options
            )
            return (
                f'<select id={id_} name={f.name}><option value="">Choose…</option>{opts}</select>'
            )
        if f.kind == "radio":
            return "".join(
                f'<label><input type=radio name={f.name} value="{html.escape(o)}"'
                f"{' checked' if value == o else ''}> {html.escape(o)}</label>"
                for o in f.options
            )
        if f.kind == "checkbox":
            checked = " checked" if value else ""
            return f"<input id={id_} type=checkbox name={f.name} value=yes{checked}>"
        if f.kind == "textarea":
            return f"<textarea id={id_} name={f.name}>{v}</textarea>"
        kind = "text" if f.kind == "date" and self.has("date_format") else f.kind
        step = " step=0.01" if kind == "number" else ""
        return f'<input id={id_} name={f.name} type={kind}{step} value="{v}">'

    def _row(self, f: Field, value: str) -> str:
        text, control = html.escape(self.label(f)), self._input(f, value)
        if f.kind == "radio":
            return f"<fieldset><legend>{text}</legend>{control}</fieldset>"
        if f.kind == "checkbox":
            return f"<p>{control} <label for=f-{f.name}>{text}</label></p>"
        return f"<p><label for=f-{f.name}>{text}</label> {control}</p>"

    def page(self, title: str, body: str) -> str:
        overlay = ""
        if self.has("overlay"):
            overlay = (
                '<div id=consent role=dialog aria-label="Cookie consent" '
                'style="position:fixed;inset:0;'
                'background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center">'
                '<div style="background:#fff;padding:2em"><p>We use cookies.</p>'
                "<button onclick=\"document.cookie='consent=1;path=/';"
                "document.getElementById('consent').remove()\">Accept all</button></div></div>"
                "<script>if(document.cookie.includes('consent=1'))"
                "document.getElementById('consent').remove()</script>"
            )
        if self.has("slow"):
            body = (
                f"<div id=later hidden>{body}</div><p id=wait>Loading…</p><script>setTimeout(()=>{{"
                "document.getElementById('later').hidden=false;document.getElementById('wait').remove()},2000)</script>"
            )
        return (
            f"<!doctype html><html><head><meta charset=utf-8><title>{html.escape(title)} - "
            f"{html.escape(self.spec.title)}</title></head><body><header><b>{html.escape(self.spec.title)}"
            f"</b></header><h1>{html.escape(title)}</h1>{body}{overlay}</body></html>"
        )

    def form(self, sid: str, page: int, values: dict[str, str], errors: list[str]) -> str:
        err = ""
        if errors:
            items = "".join(f"<li>{html.escape(e)}</li>" for e in errors)
            err = f"<div role=alert><b>Please fix:</b><ul>{items}</ul></div>"
        rows = "".join(self._row(f, values.get(f.name, "")) for f in self.fields(page))
        last = page == len(self.spec.pages) - 1
        v = 1 if self.has("relabel") else 0
        if not last:
            buttons = f"<button type=submit>{self.spec.next_label[v]}</button>"
        elif self.has("decoy"):
            buttons = (
                f"<button type=submit name=action value=draft>{self.spec.submit_label[v]}</button> "
                "<button type=submit name=action value=file>Submit for approval</button>"
            )
        elif self.has("review_page"):
            buttons = "<button type=submit>Review</button>"
        else:
            buttons = f"<button type=submit>{self.spec.submit_label[v]}</button>"
        title = f"{self.spec.title} · step {page + 1} of {len(self.spec.pages)}"
        return self.page(
            title, f"{err}<form method=post action=/form/{page}?s={sid}>{rows}{buttons}</form>"
        )

    # -- what it accepts ----------------------------------------------------------------------

    def errors(self, page: int, values: dict[str, str]) -> list[str]:
        out = []
        for f in self.fields(page):
            value = values.get(f.name, "").strip()
            if not value:
                out.append(f"{self.label(f)} is required.")
            elif f.kind in ("select", "radio") and value not in f.options:
                out.append(f"Choose one of the options for {self.label(f)}.")
            elif f.kind == "date" and parse_date(value, self.has("date_format")) is None:
                fmt = "DD/MM/YYYY" if self.has("date_format") else "a date"
                out.append(f"Enter {self.label(f)} as {fmt}.")
        if not out and self.spec.check is not None:
            out.extend(self.spec.check(page, values))
        return out

    def reference(self) -> str:
        n = sum(1 for r in self.records if r["status"] == "filed") + 1
        if self.has("ref_format"):
            return f"{self.spec.ref_prefix[1]}-2026-{n:04d}"
        return f"{self.spec.ref_prefix[0]}-{1000 + n}"

    def file(self, values: dict[str, str], status: str) -> str:
        record: dict[str, Any] = {
            "status": status,
            "values": dict(values),
            "mutations": sorted(self.mutations),
        }
        if status == "filed":
            record["ref"] = self.reference()
        self.records.append(record)
        return str(record.get("ref", ""))

    def done(self, ref: str) -> str:
        if self.has("ref_format"):
            body = f"<p>Thanks, we have it: {html.escape(ref)}.</p>"
        else:
            body = f"<p>Your reference is <strong id=ref>{html.escape(ref)}</strong>.</p>"
        return self.page(self.spec.done_heading, body + "<p><a href=/form>Start another</a></p>")


def parse_date(text: str, dmy: bool) -> date | None:
    try:
        if dmy:
            d, m, y = (int(p) for p in text.split("/"))
            return date(y, m, d)
        return date.fromisoformat(text)
    except ValueError:
        return None


def _handler(site: Site) -> type[BaseHTTPRequestHandler]:
    def _page(path: str) -> int | None:
        """The form page a /form/N path names, or None for a page this site doesn't have; an
        agent finding its way round tries addresses of its own."""
        tail = path.rsplit("/", 1)[1]
        return int(tail) if tail.isdigit() and int(tail) < len(site.spec.pages) else None

    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: str, code: int = 200, location: str | None = None) -> None:
            self.send_response(code)
            if location:
                # Built from the session id in the query; only the ids this site made get here,
                # and a line break would end the header early.
                self.send_header("Location", location.replace("\r", "").replace("\n", ""))
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self) -> None:
            url = urllib.parse.urlparse(self.path)
            query = urllib.parse.parse_qs(url.query)
            sid = query.get("s", [""])[0]
            with site._lock:
                if url.path == "/form":
                    sid = str(len(site._sessions) + 1)
                    site._sessions[sid] = {}
                    return self._send(site.form(sid, 0, {}, []))
                page = _page(url.path) if url.path.startswith("/form/") else None
                if page is not None and sid in site._sessions:
                    return self._send(site.form(sid, page, {}, []))
                if url.path == "/review" and sid in site._sessions:
                    rows = "".join(
                        f"<tr><th>{html.escape(k)}</th><td>{html.escape(v)}</td></tr>"
                        for k, v in site._sessions[sid].items()
                        if k != "action"
                    )
                    body = (
                        f"<table>{rows}</table><form method=post action=/confirm?s={sid}>"
                        "<button type=submit>Confirm and send</button></form>"
                    )
                    return self._send(site.page("Check your entries", body))
                if url.path == "/draft":
                    return self._send(
                        site.page("Draft saved", "<p>Saved as a draft. It has not been sent.</p>")
                    )
                if url.path.startswith("/done/"):
                    return self._send(site.done(url.path.rsplit("/", 1)[1]))
                if url.path == "/records":
                    return self._send(json.dumps(site.records))
            self._send(site.page("Not found", ""), 404)

        def do_POST(self) -> None:
            url = urllib.parse.urlparse(self.path)
            sid = urllib.parse.parse_qs(url.query).get("s", [""])[0]
            size = int(self.headers.get("Content-Length", 0))
            sent = {
                k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(size).decode()).items()
            }
            with site._lock:
                if sid not in site._sessions:
                    return self._send(site.page("Session expired", "<p>Start again.</p>"), 400)
                if url.path == "/confirm":
                    ref = site.file(site._sessions.pop(sid), "filed")
                    return self._send("", 303, f"/done/{ref}")
                page = _page(url.path) if url.path.startswith("/form/") else None
                if page is None:
                    return self._send(site.page("Not found", ""), 404)
                errors = site.errors(page, sent)
                if errors:
                    return self._send(site.form(sid, page, sent, errors), 422)
                action = sent.pop("action", "")
                site._sessions[sid].update(sent)
                if page + 1 < len(site.spec.pages):
                    return self._send("", 303, f"/form/{page + 1}?s={sid}")
                if site.has("decoy") and action != "file":
                    site.file(site._sessions.pop(sid), "draft")
                    return self._send("", 303, "/draft")
                if site.has("review_page"):
                    return self._send("", 303, f"/review?s={sid}")
                ref = site.file(site._sessions.pop(sid), "filed")
                return self._send("", 303, f"/done/{ref}")

        def log_message(self, *args: Any) -> None:
            pass

    return Handler


def serve(site: Site, port: int = 0) -> ThreadingHTTPServer:
    """Serve `site` on 127.0.0.1 in a background thread; the server's port is its address."""
    server = ThreadingHTTPServer(("127.0.0.1", port), _handler(site))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
