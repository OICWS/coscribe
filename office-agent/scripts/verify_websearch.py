"""Standalone (not pytest) live verification of tools/websearch.py's
web_search -- a real DuckDuckGo query, no LLM involved at all (the tool
itself is a pure function, nothing about it needs a model).

Not runnable from this project's own development sandbox: that
environment's egress proxy explicitly blocks every general web-search-
engine host at the policy level (html.duckduckgo.com, search.brave.com,
en.wikipedia.org, ... all returned "403 Forbidden" via `curl
$HTTPS_PROXY/__agentproxy/status`'s recentRelayFailures when this was
first tried), the same kind of restriction that already blocked live
DeepSeek/GLM verification earlier in this project's history -- not a code
bug, and not something to route around. Run this from an unrestricted
machine (e.g. the same one you already used to verify DeepSeek/GLM).

Run with: python scripts/verify_websearch.py
"""

from __future__ import annotations

from coscribe.tools.websearch import web_search


def main() -> None:
    results = web_search("what is the capital of France")
    print(f"Got {len(results)} result(s):\n")
    for index, result in enumerate(results, start=1):
        print(f"{index}. {result['title']}")
        print(f"   {result['url']}")
        print(f"   {result['snippet'][:150]}")
        print()

    assert results, "expected at least one result"
    assert all({"title", "url", "snippet"} <= r.keys() for r in results), (
        "every result should have title/url/snippet"
    )
    print("PASS: web_search returned real, well-shaped results from DuckDuckGo.")


if __name__ == "__main__":
    main()
