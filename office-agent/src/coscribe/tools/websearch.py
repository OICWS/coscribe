"""Live web search for grounding -- current events, facts to verify,
anything beyond what the model already knows from training. Distinct
from search_files/search_pdf (which search *local* workspace content) and
not a RAG/document-retrieval system: this queries the open web directly
through a search engine, no local index or embedding store involved.

Uses the standalone ``ddgs`` package (not langchain_community's
DuckDuckGoSearchRun, which is now considered legacy in favor of this
library) directly, the same "write our own thin tool function" pattern
every other built-in tool in this package already follows rather than
pulling in a prebuilt LangChain community tool wrapper wholesale.

Uses ddgs's own "auto" backend selection (the default -- not pinned to a
single engine) rather than forcing DuckDuckGo specifically: "auto" queries
several free, no-API-key engines in parallel (duckduckgo, brave, google,
mojeek, startpage, wikipedia, yahoo, yandex -- confirmed via
`ddgs.engines.ENGINES["text"]`, none need a key) and returns whichever
results come back, so one engine's own anti-bot response doesn't fail the
whole call. Live-reported, not hypothetical: a real run returned DuckDuckGo's
`html.duckduckgo.com/html/` responding HTTP 202 (a soft anti-bot block, not
a real result page) -- previously pinning `backend="duckduckgo"` meant that
single soft-block emptied the whole search; "auto" is the documented
escape hatch for exactly this, not a fallback that needs building later.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ddgs import DDGS

from ..runtime.proxy import configured_proxy
from ..runtime.types import tool_metadata

# `from ddgs import DDGS` above is actually `ddgs`'s own `_DDGSProxy` (a
# lazy-loading metaclass-based stand-in, see ddgs/__init__.py) -- calling
# it dispatches to the real `ddgs.ddgs.DDGS` class, imported on first use.
# Harmless for real calls, but means tests/test_websearch_tool.py has to
# monkeypatch "ddgs.ddgs.DDGS.text" (the real class), not
# "coscribe.tools.websearch.DDGS.text" (the proxy) -- confirmed the
# hard way when the "obvious" patch target silently made a real network
# call instead of using the fake.

DEFAULT_MAX_RESULTS = 5


def web_search(query: str, max_results: int = DEFAULT_MAX_RESULTS) -> list[dict[str, str]]:
    """Search the live web for current information beyond your training
    data -- news, facts to verify, anything time-sensitive. Not for
    finding things already in this workspace (use search_files/
    search_pdf instead), and not a substitute for actually reading a
    document you've already been given.

    Args:
        query: what to search for
        max_results: how many results to return (default 5)
    """
    # DDGS's constructor takes its own proxy= kwarg -- separate from the
    # DDGS_PROXY env var its docs mention, which a typical corporate
    # .env/HTTP_PROXY setup won't have. configured_proxy() forwards the
    # standard HTTP_PROXY/HTTPS_PROXY instead (see runtime/proxy.py).
    results = DDGS(proxy=configured_proxy()).text(query, max_results=max_results)
    return [
        {
            "title": str(result.get("title", "")),
            "url": str(result.get("href", "")),
            "snippet": str(result.get("body", "")),
        }
        for result in results
    ]


def build_websearch_tools() -> list[Callable[..., Any]]:
    return [tool_metadata(web_search, risk_category="READ", category="web")]
