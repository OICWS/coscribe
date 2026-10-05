"""Read the film's sound-sync events and VO timeline.

Events come from the live page: scenes call F.event(t, name, data) while they
build, and core.js publishes the sorted list as window.FILM_EVENTS. We load
index.html?render=1&lang=<lang> in headless Chromium (same way render/render.py
does), so whatever the animators add is picked up on every build.
"""
import functools
import http.server
import json
import os
import re
import threading
from pathlib import Path

FILM = Path(__file__).resolve().parent.parent
CHROMIUM = os.environ.get("FILM_CHROMIUM", "/opt/pw-browsers/chromium")


def _serve():
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    handler = functools.partial(Quiet, directory=str(FILM))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def extract_events(langs):
    """{lang: [ {t, name, ...data}, ... ]} for each language, plus page errors."""
    from playwright.sync_api import sync_playwright

    out, errs = {}, {}
    srv = _serve()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=CHROMIUM)
            for lang in langs:
                page = browser.new_page(viewport={"width": 1920, "height": 1080})
                e = []
                page.on("pageerror", lambda x, e=e: e.append(str(x)))
                page.goto(f"http://127.0.0.1:{srv.server_port}/index.html?render=1&lang={lang}&subs=1")
                page.wait_for_function("window.FILM_EVENTS !== undefined", timeout=60000)
                out[lang] = page.evaluate("JSON.parse(JSON.stringify(window.FILM_EVENTS))")
                errs[lang] = e
                page.close()
            browser.close()
    finally:
        srv.shutdown()
    return out, errs


def load_vo():
    """content/vo.js is strict JSON after the first '='."""
    src = (FILM / "content" / "vo.js").read_text(encoding="utf-8")
    body = src[src.index("=", src.index("window.FILM_VO")) + 1:]
    body = body.strip().rstrip(";").strip()
    return json.loads(body)


if __name__ == "__main__":
    import sys
    from collections import Counter

    ev, errs = extract_events(sys.argv[1:] or ["zh", "en"])
    for lang, lst in ev.items():
        print(lang, len(lst), dict(Counter(e["name"] for e in lst)), "errors:", errs[lang][:3])
        for e in lst:
            if e["name"] != "key":
                print("  ", e)
