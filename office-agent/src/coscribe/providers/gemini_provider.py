# Vendored from aisuite's GitHub `main` branch (MIT licensed), which is not part of
# the aisuite package published to PyPI (see ARCHITECTURE.md and
# coscribe/runtime/llm_client.py for why we route "gemini:" models here instead
# of through aisuite.provider.ProviderFactory).
#
# Gemini provider — Google's Gemini Developer API (API key), via the google-genai SDK.
# Distinct from the `google` provider, which targets Vertex AI (GCP project auth).
#
# This file used to also hold a full pair of converters between the OpenAI-shaped
# interface and Gemini's generateContent format (system-instruction/role mapping,
# thought_signature smuggling through synthesized tool-call ids, OpenAPI-subset
# schema sanitizing, streaming chunk assembly, 429 retry handling) -- real,
# hand-vendored code that shipped three real bugs in a row (a str|None/int|None
# schema bug, a missing-thought_signature-on-replay bug, a bytes-vs-base64-text
# type bug) and was the direct motivation for this project's migration off its own
# hand-rolled agent runtime onto LangChain/LangGraph (see runtime_lg/README.md's
# Context section). Deleted as dead code once that migration finished and
# confirmed nothing reachable from a real entry point still called it -- only
# get_context_window (below) is: LLMClient.get_context_window's best-effort
# max-input-token lookup, used purely for a UI indicator, never for an actual
# model call (runtime_lg's own resolve_chat_model, via langchain-google-genai,
# handles every real Gemini conversation now). See runtime_lg/README.md's
# "audit + delete old runtime" section.

import os

from aisuite.provider import Provider


class GeminiProvider(Provider):
    def __init__(self, **config):
        """Initialize the Gemini provider. The API key comes from the config or the
        GEMINI_API_KEY / GOOGLE_API_KEY environment variables."""
        config.setdefault(
            "api_key", os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        )
        if not config["api_key"]:
            raise ValueError(
                "Gemini API key is missing. Please provide it in the config or set "
                "the GEMINI_API_KEY environment variable."
            )
        # Lazy import so the SDK is only required when this provider is used.
        from google import genai

        self.client = genai.Client(**config)

    def get_context_window(self, model):
        """The model's real max input-token limit, straight from the API
        (not a guessed constant) -- see runtime/llm_client.py's
        get_context_window for how this is used and what falls back when
        it's unavailable."""
        return self.client.models.get(model=model).input_token_limit

    def chat_completions_create(self, model, messages, **kwargs):
        """Real, live-caught regression: aisuite's own Provider base class
        marks this abstract (@abstractmethod), so deleting it entirely (as
        the dead-code audit did) made GeminiProvider un-instantiable --
        `TypeError: Can't instantiate abstract class GeminiProvider with
        abstract method chat_completions_create` -- breaking the one method
        that's still alive (get_context_window) too, since both are called
        through the same constructed instance. This stub only exists to
        satisfy that ABC requirement; the real conversion logic it used to
        contain is gone for good (see this module's header comment) and
        nothing reachable from a real entry point should ever call this."""
        raise NotImplementedError(
            "GeminiProvider.chat_completions_create was deleted as dead code -- "
            "real Gemini conversations go through langchain-google-genai now, "
            "see runtime_lg/README.md's 'audit + delete old runtime' section."
        )
