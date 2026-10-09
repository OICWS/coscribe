"""The providers the app knows about: the built-in ones and the OpenAI-compatible
ones offered as a convenience, with the .env variables each uses."""

from __future__ import annotations

# Convenience pre-fills for a few well-known OpenAI-compatible providers --
# not an exhaustive list. Any OpenAI-compatible endpoint works via the
# "Add custom provider" form below; these just save typing the base_url.
# Deliberately NO "default_model" guess here (unlike the builtin anthropic/
# gemini entries in get_providers_catalog below, whose model IDs this
# project's own release cadence controls) -- a third-party vendor's model
# lineup is entirely outside this project's control and turns over on its
# own schedule (real example: this catalog previously hardcoded DeepSeek's
# default_model as "deepseek-v4-flash", which was already wrong -- live
# user report named the real current models as "deepseek-flash"/
# "deepseek-v4-pro"). A wrong guessed model name silently pre-filled into
# the Providers tab's form is worse than an empty field the user has to
# fill in themselves from the vendor's own current docs: it looks
# authoritative but isn't, and a user who doesn't second-guess it gets a
# confusing "model not found"-shaped failure instead of an obviously-
# blank field asking for input. base_url is different -- an API host
# essentially never changes, so that part of each entry below is still a
# real, low-risk time-saver, verified against each vendor's docs.
PROVIDER_CATALOG = [
    {
        "name": "deepseek",
        "description": "DeepSeek's OpenAI-compatible API.",
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "",
    },
    {
        "name": "kimi",
        "description": "Moonshot AI's Kimi, OpenAI-compatible API.",
        "base_url": "https://api.moonshot.ai/v1",
        "default_model": "",
    },
    {
        "name": "glm",
        "description": "Zhipu's GLM, OpenAI-compatible API.",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "",
    },
    {
        "name": "ollama",
        # add_provider (below) still requires a non-blank API key for every
        # custom provider, gated tools included -- Ollama's own OpenAI-
        # compatible server never actually checks the Authorization header,
        # so any placeholder text works; called out explicitly here since
        # it's the one entry in this catalog where the field means nothing
        # to the provider it's configuring, unlike a real cloud API key.
        "description": (
            "Ollama's local OpenAI-compatible API -- fully offline, no cloud "
            'account. API key can be any placeholder text (e.g. "ollama"), '
            "it isn't actually checked. Fill in Default Model yourself with "
            "whatever you've already pulled via `ollama pull <model>` -- "
            "coscribe can't see what's installed, so there's no safe guess "
            "to pre-fill here."
        ),
        "base_url": "http://localhost:11434/v1",
        "default_model": "",
    },
]

# Built-in providers: each has a raw (unprefixed) API-key env var -- read
# directly by each provider SDK, not modeled on Settings (see config.py's
# Settings docstring); values are never sent to the browser in full, only
# masked -- and an *optional*, COSCRIBE_-prefixed "default model"
# companion. The companion is deliberately COSCRIBE_-prefixed, unlike
# the key itself: no provider SDK reads
# "COSCRIBE_ANTHROPIC_DEFAULT_MODEL" -- it's pure coscribe
# bookkeeping (which model to offer as this provider's quick-pick in the
# model-picker / Providers tab), exactly what that prefix is used for
# everywhere else. Deliberately NOT added to COSCRIBE_ENV_VARS/Settings:
# get_config/get_providers below read it fresh from dotenv_values(".env")
# on every request, so (unlike every COSCRIBE_ENV_VARS entry) it never
# needs a restart to take effect -- see update_config's restart_required
# computation.
BUILTIN_PROVIDERS: list[dict[str, str]] = [
    {
        "key": "anthropic",
        "api_key_env": "ANTHROPIC_API_KEY",
        "default_model_env": "COSCRIBE_ANTHROPIC_DEFAULT_MODEL",
    },
    {
        "key": "openai",
        "api_key_env": "OPENAI_API_KEY",
        "default_model_env": "COSCRIBE_OPENAI_DEFAULT_MODEL",
    },
    {
        "key": "gemini",
        "api_key_env": "GEMINI_API_KEY",
        "default_model_env": "COSCRIBE_GEMINI_DEFAULT_MODEL",
    },
]

PROVIDER_KEY_ENV_VARS = [p["api_key_env"] for p in BUILTIN_PROVIDERS]
PROVIDER_DEFAULT_MODEL_ENV_VARS = [p["default_model_env"] for p in BUILTIN_PROVIDERS]
