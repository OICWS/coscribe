""""provider:model" string -> LangChain chat model, mirroring
runtime/llm_client.py's LLMClient._resolve_provider shape and the same
provider-string convention (so Settings.default_model doesn't need to
change format if this ever replaces the old runtime).

Phase 1 scope: anthropic, gemini, and one generic OpenAI-compatible path
(reusing runtime/provider_config.py's existing custom-provider config
loading, unmodified). Not wired into cli.py/web/coordinator.py yet.
"""

from __future__ import annotations

from typing import Any

from pydantic import SecretStr


def resolve_chat_model(
    model: str, custom_providers: dict[str, dict[str, str]] | None = None
) -> Any:
    """Return a LangChain BaseChatModel for a "provider:model" string.

    custom_providers is the same {name: {"base_url", "api_key"}} shape
    runtime/provider_config.py's load_custom_providers already produces --
    passed through untouched so this can share a config file with the old
    runtime during any future side-by-side period.
    """
    if ":" not in model:
        raise ValueError(f'model must be a "provider:model" string, got {model!r}')
    provider_key, model_name = model.split(":", 1)
    custom_providers = custom_providers or {}

    if provider_key in custom_providers:
        from langchain_openai import ChatOpenAI

        config = custom_providers[provider_key]
        return ChatOpenAI(
            model=model_name,
            base_url=config["base_url"],
            api_key=SecretStr(config["api_key"]),
            # ChatOpenAI only auto-enables streamed usage_metadata when
            # base_url is unset (the real OpenAI endpoint) -- confirmed by
            # reading langchain_openai's own __init__ (its auto-enable
            # condition checks base_url is None). Any custom
            # OpenAI-compatible provider (DeepSeek, Kimi, GLM, ...) always
            # sets base_url, so without this explicit flag the usage bar
            # (web/session.py's _stream_turn) would silently never populate
            # for these providers specifically, unlike gemini:/anthropic:/
            # openai: -- see runtime_lg/README.md's "usage bar" section.
            stream_usage=True,
        )
    if provider_key == "anthropic":
        from langchain_anthropic import ChatAnthropic

        # ChatAnthropic's pydantic field is `model` aliased to `model_name` --
        # only the alias is accepted as a constructor kwarg (confirmed via
        # ChatAnthropic.model_fields; mypy rejects `model=` here). timeout/stop
        # are genuinely optional at runtime (pydantic fields with None
        # defaults) but mypy's generated __init__ stub treats them as
        # required without the pydantic mypy plugin configured -- passing
        # None explicitly is the accurate, not-a-workaround value anyway.
        return ChatAnthropic(model_name=model_name, timeout=None, stop=None)
    if provider_key == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        # NOT forcing transport="rest" anymore -- see git history for
        # why that was tried and reverted. Short version: it was based
        # on an incomplete diagnosis (assumed gRPC doesn't respect
        # HTTP_PROXY/HTTPS_PROXY reliably), which turned out to be
        # wrong -- gRPC's own docs confirm native support for
        # authenticated CONNECT proxies via these exact env vars
        # (RFC7617 Basic auth, username:password embedded in the URL,
        # same format this project's own .env convention already uses).
        # The real bug behind the original corporate-proxy hang was
        # `.env` not overriding an already-set OS environment variable
        # (python-dotenv's own override=False default -- see cli.py's
        # _load_settings), fixed separately. Forcing transport="rest"
        # instead traded that for a real bug in this SDK version's own
        # REST-async streaming path -- live-hit: "TypeError: object
        # ResponseIterator can't be used in 'await' expression" --
        # `langchain_google_genai`'s own retry-wrapping helper awaits
        # the return value of a streaming call as if it were a plain
        # coroutine, when a streaming REST call actually returns an
        # async-iterable object directly. That's an unusual, lightly-
        # exercised combination (most users hit either plain gRPC or a
        # genuinely custom Vertex endpoint, not "force REST against the
        # real default Gemini Developer API endpoint"). Reverting to
        # the plain default lets async calls use grpc_asyncio again,
        # the library's own best-tested path, now that the actual root
        # cause (.env not applying) is fixed at the source.
        #
        # max_retries=2, not the library's own default of 6: a live,
        # user-reported bug, root-caused by reading
        # langchain_google_genai's own retry decorator (chat_models.py,
        # _create_retry_decorator) -- it retries ResourceExhausted (a
        # quota error) with the same exponential backoff as a genuinely
        # transient one, up to ~60s of total waiting across 6 attempts.
        # A daily-quota-exhausted account will not recover within that
        # window no matter how many times it's retried, so those 6
        # attempts are pure dead time -- and during all of it, nothing
        # in this app can interrupt the call (session.py's stop flag is
        # only checked between already-yielded stream chunks, none of
        # which exist while stuck inside this retry loop), so the user
        # sees a spinner that won't stop and a model switch that
        # silently has no effect on the stuck turn. Two attempts keeps
        # a little real benefit for an actually-transient 429/503
        # (~1-2s of backoff) while capping the worst case at a few
        # seconds instead of over a minute. Previously observed and
        # documented (not fixed) in this exact shape -- see this
        # package's own README.md, "burning through most of a run's
        # remaining budget on one 429 before the process gave up."
        return ChatGoogleGenerativeAI(model=model_name, max_retries=2)
    if provider_key == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model_name)

    raise ValueError(
        f"Unsupported provider {provider_key!r} in runtime_lg Phase 1 -- "
        "only anthropic/gemini/openai/custom-openai-compatible are wired up."
    )


def with_prompt_cache_key(model: Any, key: str) -> Any:
    """OpenAI routes requests that share a prompt_cache_key to the same
    cache, so one key per conversation keeps its growing prefix warm
    (Codex sends its session id the same way). Only OpenAI's own endpoint:
    an OpenAI-compatible provider may reject a parameter it doesn't know."""
    from langchain_openai import ChatOpenAI

    if not isinstance(model, ChatOpenAI) or model.openai_api_base is not None:
        return model
    return model.model_copy(
        update={"model_kwargs": {**model.model_kwargs, "prompt_cache_key": key}}
    )
