import os
from types import SimpleNamespace

import pytest

from coscribe.runtime.llm_client import _VENDORED_PROVIDERS, LLMClient


def test_gemini_provider_is_actually_instantiable_through_the_real_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for a real, live-caught bug in the dead-code audit
    that trimmed providers/gemini_provider.py: deleting
    chat_completions_create entirely left GeminiProvider unable to satisfy
    aisuite's own Provider(ABC) (`chat_completions_create` is
    `@abstractmethod` there), so `GeminiProvider(**config)` raised
    `TypeError: Can't instantiate abstract class GeminiProvider with
    abstract method chat_completions_create` -- breaking get_context_window
    too, the one method that's supposed to still work, since both are only
    reachable through a constructed instance. Every other test for this
    path (test_get_context_window_*) monkeypatches _VENDORED_PROVIDERS
    with a fake, so none of them actually construct the real class -- this
    is the one live-caught gap those didn't cover. No network call happens
    at construction time (only get_context_window's own API call would),
    so a fake API key is enough here."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-construction-only")
    client = LLMClient()

    provider, model_name = client._resolve_provider("gemini:gemini-3.1-flash-lite")

    assert model_name == "gemini-3.1-flash-lite"
    assert hasattr(provider, "get_context_window")


def test_invalidate_provider_forces_a_rebuild_that_picks_up_a_changed_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression test for a real bug found live: pasting a correct Gemini
    # API key into Settings after an earlier attempt with no key (or a
    # different one) still failed with "API key not valid" -- because
    # _resolve_provider caches the constructed provider instance, and each
    # built-in provider's own SDK constructor reads its key from os.environ
    # exactly once (confirmed for the vendored GeminiProvider -- its
    # __init__ does `os.getenv("GEMINI_API_KEY")`), not on every call.
    # web/app.py's update_config writes a freshly pasted key into
    # os.environ, but that alone is not enough: the *cached* provider
    # instance from an earlier resolution never re-reads it. A fake
    # vendored provider that captures whatever key is in os.environ at
    # construction time stands in for that same real behavior.
    monkeypatch.setenv("FAKE_API_KEY", "bad-key")
    monkeypatch.setitem(
        _VENDORED_PROVIDERS,
        "fake",
        lambda config: SimpleNamespace(api_key=os.environ["FAKE_API_KEY"]),
    )
    client = LLMClient()
    stale_provider, _ = client._resolve_provider("fake:model")
    assert stale_provider.api_key == "bad-key"

    monkeypatch.setenv("FAKE_API_KEY", "good-key")
    still_stale_provider, _ = client._resolve_provider("fake:model")
    assert still_stale_provider.api_key == "bad-key"  # proves the cache is the problem

    client.invalidate_provider("fake")
    fresh_provider, _ = client._resolve_provider("fake:model")

    assert fresh_provider.api_key == "good-key"


def test_custom_provider_resolves_to_openai_compatible_client() -> None:
    from aisuite.providers.openai_provider import OpenaiProvider

    client = LLMClient(
        custom_providers={
            "deepseek": {"base_url": "https://api.deepseek.com/v1", "api_key": "sk-test"}
        }
    )

    provider, model_name = client._resolve_provider("deepseek:deepseek-v4-flash")

    assert isinstance(provider, OpenaiProvider)
    # openai.OpenAI normalizes base_url with a trailing slash.
    assert str(provider.client.base_url).rstrip("/") == "https://api.deepseek.com/v1"
    assert model_name == "deepseek-v4-flash"


def test_custom_provider_takes_priority_over_vendored_and_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aisuite.providers.openai_provider import OpenaiProvider

    # "gemini" is normally vendored (see _VENDORED_PROVIDERS); a custom
    # provider registered under the same key should win, since
    # _resolve_provider checks custom_providers first.
    client = LLMClient(
        custom_providers={"gemini": {"base_url": "https://example.com/v1", "api_key": "k"}}
    )

    provider, _ = client._resolve_provider("gemini:some-model")

    assert isinstance(provider, OpenaiProvider)


def test_register_custom_provider_makes_it_immediately_resolvable() -> None:
    from aisuite.providers.openai_provider import OpenaiProvider

    client = LLMClient()

    client.register_custom_provider(
        "deepseek", {"base_url": "https://api.deepseek.com/v1", "api_key": "sk-test"}
    )
    provider, model_name = client._resolve_provider("deepseek:deepseek-v4-flash")

    assert isinstance(provider, OpenaiProvider)
    assert str(provider.client.base_url).rstrip("/") == "https://api.deepseek.com/v1"
    assert model_name == "deepseek-v4-flash"


def test_register_custom_provider_replaces_a_stale_cached_instance() -> None:
    client = LLMClient(
        custom_providers={"deepseek": {"base_url": "https://old.example/v1", "api_key": "sk-old"}}
    )
    client._resolve_provider("deepseek:model")  # populates the _providers cache

    client.register_custom_provider(
        "deepseek", {"base_url": "https://new.example/v1", "api_key": "sk-new"}
    )
    provider, _ = client._resolve_provider("deepseek:model")

    # If _resolve_provider had kept serving the cached instance built from
    # the old config, this would still say old.example.
    assert str(provider.client.base_url).rstrip("/") == "https://new.example/v1"


def test_deregister_custom_provider_removes_it(monkeypatch: pytest.MonkeyPatch) -> None:
    # A vendored fallback under the same key, distinguishable from the
    # custom OpenaiProvider config -- proves deregistering genuinely falls
    # through past _custom_providers, rather than picking a real provider
    # name (e.g. "deepseek") where aisuite's own built-in provider for that
    # name could quietly succeed too and mask the assertion.
    fallback = object()
    monkeypatch.setitem(_VENDORED_PROVIDERS, "fake", lambda config: fallback)
    client = LLMClient(
        custom_providers={"fake": {"base_url": "https://example.com/v1", "api_key": "k"}}
    )
    client._resolve_provider("fake:model")  # populate the cache too

    client.deregister_custom_provider("fake")
    provider, _ = client._resolve_provider("fake:model")

    assert provider is fallback


class _ProviderWithNoLiveContextWindowLookup:
    """A provider with no get_context_window method at all -- stands in for
    every non-Gemini provider, all of which fall back to
    _FALLBACK_CONTEXT_WINDOWS/_DEFAULT_CONTEXT_WINDOW."""


class _ProviderWithContextWindow:
    def get_context_window(self, model):  # type: ignore[no-untyped-def]
        return 999_000


class _ProviderThatFailsContextWindowLookup:
    def get_context_window(self, model):  # type: ignore[no-untyped-def]
        raise RuntimeError("network hiccup")


def test_get_context_window_uses_the_providers_live_lookup_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(_VENDORED_PROVIDERS, "fake", lambda config: _ProviderWithContextWindow())
    client = LLMClient()

    assert client.get_context_window("fake:model") == 999_000


def test_get_context_window_falls_back_when_provider_has_no_live_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        _VENDORED_PROVIDERS, "fake", lambda config: _ProviderWithNoLiveContextWindowLookup()
    )
    client = LLMClient()

    assert client.get_context_window("fake:claude-3") == 200_000
    assert client.get_context_window("fake:gpt-4o") == 128_000
    # Real, user-reported bug: deepseek-flash (a real, current DeepSeek
    # model -- verified live against api-docs.deepseek.com, 1M-token
    # context) had no entry here, so it silently fell through to the
    # generic 128k default -- an 8x-too-small number shown right on the
    # context-window ring for a real, configured provider.
    assert client.get_context_window("fake:deepseek-flash") == 1_000_000
    assert client.get_context_window("fake:some-unknown-model") == 128_000


def test_get_context_window_falls_back_when_live_lookup_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        _VENDORED_PROVIDERS, "fake", lambda config: _ProviderThatFailsContextWindowLookup()
    )
    client = LLMClient()

    assert client.get_context_window("fake:model") == 128_000
