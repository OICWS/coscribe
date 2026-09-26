import pytest

pytest.importorskip("langchain_openai")

from coscribe.runtime_lg.providers import resolve_chat_model, with_prompt_cache_key  # noqa: E402


def test_openai_gets_the_conversation_cache_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    model = with_prompt_cache_key(resolve_chat_model("openai:gpt-5"), "thread-1")
    assert model.model_kwargs["prompt_cache_key"] == "thread-1"


def test_openai_compatible_providers_are_left_alone() -> None:
    providers = {"deepseek": {"base_url": "https://api.deepseek.com", "api_key": "k"}}
    model = with_prompt_cache_key(resolve_chat_model("deepseek:deepseek-flash", providers), "t")
    assert "prompt_cache_key" not in model.model_kwargs
