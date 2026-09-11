"""Thin wrapper around aisuite's provider layer, kept alive today for just
one thing: get_context_window()'s best-effort max-input-token lookup (see
web/session.py's send_state / web/app.py, both of which use it purely for
a UI indicator, never for an actual model call).

This used to also drive every real chat turn (`complete`/`complete_stream`,
talking to the provider directly for one turn at a time rather than
aisuite's own auto-executing `Client.chat.completions.create()`, so a
custom ToolPolicy gate could sit in between) -- deleted as dead code once
runtime_lg's own LangChain chat models (resolve_chat_model) became the
only real generation path left running. See runtime_lg/README.md's "audit
+ delete old runtime" section.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def _create_gemini_provider(config: dict[str, Any]) -> Any:
    # Vendored, not part of the published aisuite package — see
    # coscribe/providers/gemini_provider.py for why.
    from ..providers.gemini_provider import GeminiProvider

    return GeminiProvider(**config)  # type: ignore[no-untyped-call]  # vendored, not type-checked


def _create_openai_compatible_provider(config: dict[str, Any]) -> Any:
    # aisuite's own OpenaiProvider forwards its whole config dict straight to
    # openai.OpenAI(**config), which already accepts a base_url kwarg -- so
    # any OpenAI-compatible endpoint (DeepSeek, Kimi, GLM, ...) is served by
    # this one real, already-installed provider class, no new HTTP code needed.
    from aisuite.providers.openai_provider import OpenaiProvider

    return OpenaiProvider(**config)


# Providers not in the published aisuite package at all (just Gemini --
# see providers/gemini_provider.py). Keyed the same way as the
# "provider:model" string's prefix; checked before falling back to
# aisuite.provider.ProviderFactory (which handles anthropic/openai/etc.
# directly -- anthropic used to route through a small vendored subclass
# here too, adding prompt-caching cache_control breakpoints to its dead
# chat_completions_create method; deleted along with that method once it
# was confirmed unreachable, since the subclass added nothing else -- see
# runtime_lg/README.md's "audit + delete old runtime" section).
_VENDORED_PROVIDERS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "gemini": _create_gemini_provider,
}

# Unverified, best-effort fallback for get_context_window() -- only used
# when the provider itself can't report a real number (currently just
# Gemini can, via GeminiProvider.get_context_window's live API lookup).
# These are rough, may drift as vendors change their models, and should
# not be trusted as precise -- update as better information turns up.
_FALLBACK_CONTEXT_WINDOWS: list[tuple[str, int]] = [
    ("claude", 200_000),
    ("gpt", 128_000),
    ("o1", 128_000),
    ("o3", 128_000),
]
_DEFAULT_CONTEXT_WINDOW = 128_000


class LLMClient:
    def __init__(self, custom_providers: dict[str, dict[str, str]] | None = None) -> None:
        """`custom_providers` is a {name: {"base_url", "api_key"}} map of
        user-added OpenAI-compatible providers (DeepSeek, Kimi, GLM, or any
        other) -- see runtime/provider_config.py for how it's loaded from disk."""
        self._custom_providers = custom_providers or {}
        self._providers: dict[str, Any] = {}

    def register_custom_provider(self, name: str, config: dict[str, str]) -> None:
        """Add or replace a custom OpenAI-compatible provider entry, effective
        immediately for the next _resolve_provider() call under this name --
        no restart, no new LLMClient instance. Pops any already-cached
        provider instance under `name` (self._providers), so a re-add with a
        changed api_key/base_url doesn't keep serving a provider object built
        from the old config -- _resolve_provider only rebuilds when
        self._providers doesn't already have the key. Called by web/app.py's
        POST /api/providers handler immediately after it persists the same
        entry to providers.json, so the on-disk file and this in-memory dict
        never drift within one running process."""
        self._custom_providers[name] = config
        self._providers.pop(name, None)

    def deregister_custom_provider(self, name: str) -> None:
        """Undo register_custom_provider -- called by web/app.py's DELETE
        /api/providers/{name} handler right after it removes the same entry
        from providers.json. Pops from both dicts: _custom_providers (so a
        future _resolve_provider(f"{name}:...") call falls through to
        _VENDORED_PROVIDERS/ProviderFactory instead of reusing the removed
        config) and _providers (the cached instance, if any turn already
        resolved and cached one)."""
        self._custom_providers.pop(name, None)
        self._providers.pop(name, None)

    def invalidate_provider(self, name: str) -> None:
        """Drop any cached instance under `name` (self._providers), forcing
        the next _resolve_provider() call to rebuild it from scratch.

        For a *built-in* provider (anthropic/openai/gemini), unlike a custom
        one, LLMClient has no dedicated register/deregister method -- its
        config (an API key) lives in a raw env var
        (ANTHROPIC_API_KEY/OPENAI_API_KEY/GEMINI_API_KEY) that each
        provider's own SDK constructor reads directly, once, at
        construction time (confirmed for the vendored GeminiProvider: its
        __init__ does `os.getenv("GEMINI_API_KEY")`). web/app.py's
        update_config already writes a freshly-pasted key into the live
        os.environ so the *next* resolution can see it -- but without this,
        a provider resolved even once before that (e.g. the very first
        chat turn, or send_state()'s context-window lookup, with no key or
        a stale one configured yet) stays cached forever, silently
        continuing to use its original, now-stale key no matter how many
        times the user updates it in Settings. Call this right after
        writing a provider's key env var. Safe to call even if nothing is
        cached under `name` yet."""
        self._providers.pop(name, None)

    def _resolve_provider(self, model: str) -> tuple[Any, str]:
        if ":" not in model:
            raise ValueError(f'model must be a "provider:model" string, got {model!r}')
        provider_key, model_name = model.split(":", 1)
        provider = self._providers.get(provider_key)
        if provider is None:
            if provider_key in self._custom_providers:
                provider = _create_openai_compatible_provider(self._custom_providers[provider_key])
            elif provider_key in _VENDORED_PROVIDERS:
                provider = _VENDORED_PROVIDERS[provider_key]({})
            else:
                # Lazy, not top-level -- see this module's docstring update
                # (runtime_lg/README.md's startup-time section): importing
                # *any* symbol from aisuite unconditionally pulls in its own
                # Client/MCPClient machinery (aisuite/__init__.py's own
                # `from .client import Client`), which itself imports the
                # full `mcp` SDK a second time -- real, measured cost, not
                # free. Deferred here to first real use of a built-in
                # anthropic/openai provider (this branch is never reached
                # for gemini: or a custom provider, both handled above by
                # already-lazy paths), instead of paying it on every
                # process startup regardless of whether it's ever needed.
                from aisuite.provider import ProviderFactory

                provider = ProviderFactory.create_provider(provider_key, {})
            self._providers[provider_key] = provider
        return provider, model_name

    def get_context_window(self, model: str) -> int:
        """Best-effort max input-token size for `model`.

        Queries the live provider when it exposes a `get_context_window`
        method (currently just the vendored Gemini provider, via a real
        `client.models.get()` API call -- see gemini_provider.py). Falls
        back to `_FALLBACK_CONTEXT_WINDOWS`/`_DEFAULT_CONTEXT_WINDOW` for
        providers without one, or if the live lookup fails for any reason
        -- this is a nice-to-have UI indicator, never worth breaking a
        chat turn over.
        """
        provider, model_name = self._resolve_provider(model)
        live_lookup = getattr(provider, "get_context_window", None)
        if live_lookup is not None:
            try:
                return int(live_lookup(model_name))
            except Exception:  # noqa: BLE001 -- any failure just falls through to the estimate
                pass
        lowered = model.lower()
        for needle, size in _FALLBACK_CONTEXT_WINDOWS:
            if needle in lowered:
                return size
        return _DEFAULT_CONTEXT_WINDOW
