"""Application settings, loaded from environment variables and .env."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for coscribe.

    Provider API keys (ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, ...)
    are read directly by each provider's underlying SDK from the process
    environment, so they are intentionally not modeled here. `env_file`
    below only feeds *this class's own* `COSCRIBE_`-prefixed fields --
    it does not put unprefixed keys like GEMINI_API_KEY into the real
    process environment for those SDKs to see. cli.py's and web/app.py's
    entry points each call `dotenv.load_dotenv()` explicitly, before
    constructing Settings, to actually do that.
    """

    model_config = SettingsConfigDict(
        env_prefix="COSCRIBE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    default_model: str
    """Model for the Coordinator agent, as an aisuite "provider:model" string."""

    workspace_root: Path = Path("./workspace")
    """Directory the built-in file tools may read/write under."""

    extra_readable_dirs: Annotated[list[Path], NoDecode] = []
    """Directories outside workspace_root the built-in file tools (list_files/
    read_file/search_files/read_docx/read_xlsx/read_pptx/search_pdf/...) may
    also read from -- e.g. your Downloads folder, so a file a browser-
    automation MCP server just downloaded doesn't need to be copied into the
    workspace first. Comma-separated in .env. Read-only: writing still fails
    here unless the same directory also appears in extra_writable_dirs.
    """

    extra_writable_dirs: Annotated[list[Path], NoDecode] = []
    """Same idea as extra_readable_dirs, but the file tools may also write
    here (write_file/write_docx/write_xlsx/write_pptx, creating or
    overwriting files) -- implicitly readable too, no need to list a
    directory in both. Comma-separated in .env. This is real write access
    outside the sandboxed workspace; only point it at directories you trust
    the Coordinator to modify.
    """

    state_dir: Path = Path(".coscribe/state")
    """Directory task/scheduled-task state and (via runtime_lg's own checkpointer)
    conversation history are persisted to."""

    mcp_config_path: Path | None = None
    """Path to a JSON file with a Claude-Desktop-style `mcpServers` map. Each
    server entry is validated by tools/mcp.py's own validator (command/args/env
    for stdio, or server_url/headers for http) -- a small vendored replacement
    for what used to be aisuite's MCP config schema, dropped for a real
    startup-time cost, see tools/mcp.py's docstring. Unset = no MCP servers
    connected.
    """

    providers_config_path: Path | None = None
    """Path to a JSON file with a {"providers": {name: {base_url, api_key,
    default_model}}} map -- user-added OpenAI-compatible LLM providers (e.g.
    DeepSeek, Kimi, GLM, or any other) beyond the built-in anthropic/openai/
    gemini ones. Unset = no custom providers.
    """

    exec_policy_path: Path | None = None
    """Path to a JSON file with a {"rules": [{"pattern", "decision",
    "justification"}]} list, deciding whether a run_python_script/
    run_node_script call can skip live approval -- see
    runtime_lg/exec_policy.py's own docstring for the rule shape and why
    this is a human-authored allowlist, not something coscribe infers on
    its own. Unset = every call still asks, same as before this setting
    existed.
    """

    skills_dir: Path = Path("./skills")
    """Directory of Skill subdirectories, each with a SKILL.md (YAML frontmatter
    name+description, then markdown instructions -- same convention as Claude
    Code's skills). Auto-created like workspace_root; empty/missing = no skills.
    """

    custom_templates_dir: Path = Path("./templates")
    """Directory of user/deployment-local PowerPoint templates -- same shape
    as coscribe's own bundled ones (a subdirectory per template, each with
    template.pptx + template.yaml), populated either by hand or by
    extract_pptx_template distilling one from a reference deck the user
    provides. Auto-created like workspace_root/skills_dir; empty/missing =
    no custom templates, just the bundled four.
    """

    memory_path: Path = Path("./MEMORY.md")
    """File of durable facts the Coordinator remembers across sessions (distinct
    from a thread's own conversation history). Auto-created like workspace_root/skills_dir;
    empty/missing = no memory yet. The Coordinator can append to it via the
    remember tool.
    """

    hooks_config_path: Path | None = None
    """Path to a JSON file with lists of shell commands per lifecycle event
    -- see runtime/hooks.py's HOOK_EVENTS for the full, current set
    (PreToolUse, PostToolUse, SessionStart, SessionEnd, UserPromptSubmit,
    PreCompact, PostCompact, Interrupt). Each command receives a JSON
    payload on stdin describing the event; for PreToolUse, a nonzero exit
    denies the tool call (stderr becomes the reason shown to the model) --
    every other event is observational, a nonzero exit is only logged, never
    blocks whatever it's reporting on. Unset = no hooks. Hooks run arbitrary
    shell commands with your own permissions -- only point this at
    configuration you trust, same trust model as MCP server commands.
    """

    log_level: str = "INFO"

    auto_compact_threshold: float = 0.8
    """Fraction of the model's context window at which a thread would be
    automatically compacted after a turn completes, using the same
    summarize-then-collapse flow as the manual /compact command.

    Wired in via web/session.py's ChatSessionLG._build_lg_agent, which
    passes a `SummarizationMiddleware(trigger=("tokens", N))` to
    build_langgraph_agent, with N computed as
    `LLMClient.get_context_window(model) * auto_compact_threshold` -- not
    the middleware's own native `trigger=("fraction", X)` mode, which
    requires model-profile data unavailable for Gemini/custom-OpenAI-
    compatible providers (see runtime_lg/README.md's "max_turns and
    auto_compact_threshold" section for why). Only applies to the
    top-level conversation loop, same as the old runtime's
    maybe_auto_compact did -- spawn_agent/review_work's own sub-agents
    never ran it either. Manual /compact (web/session.py's
    _handle_compact) still works independently of this threshold.
    """

    max_turns: int = 20
    """Upper bound on how many tool-calling turns a single agent loop runs
    before giving up -- caps runaway token/time spend from a loop that
    keeps calling tools without finishing.

    Wired in via web/session.py's ChatSessionLG._build_lg_agent, which
    passes `ModelCallLimitMiddleware(run_limit=max_turns,
    exit_behavior="end")` to build_langgraph_agent -- the run ends
    gracefully with an injected message instead of raising. Only applies
    to the top-level conversation loop; spawn_agent/review_work's own
    sub-agents use their own independent, unrelated turn cap (a
    spawn_agent function parameter, not this setting), same scope the old
    runtime's Runner used. See runtime_lg/README.md's "max_turns and
    auto_compact_threshold" section.
    """

    auto_title_threads: bool = True
    """Name a conversation from its first exchange (a short model call in
    the background after the first finished turn), unless it already has
    a title. Off keeps the first message as the sidebar's label."""

    defer_tools: bool = True
    """When True, only `coordinator.CORE_TOOL_NAMES` stay bound to the
    top-level conversation's model by default -- everything else (most
    of coscribe's own 95 built-in tools, all MCP tools) is hidden until
    the model calls `search_tools(query)` to find it, then stays
    available for the rest of that conversation. See runtime_lg/
    tool_deferral.py's own module docstring for the full design and
    ROADMAP.md's Phase 8ap for the measured cost this addresses (~27k
    tokens of tool JSON schema alone, before this).

    On by default: connectors made it unaffordable to bind everything --
    a first "hello" with Playwright, Office 365 and a few more connected
    measured 393.8k tokens of context. The risk it trades against is a
    *capability regression*: a model that doesn't search for a tool it
    needed just silently doesn't use it. search_tools' own description
    carries an index of what's hidden (each group, a count, a few names)
    so the model knows what there is to search for. Wired in via web/session.py's
    ChatSessionLG._build_lg_agent, which passes this straight through to
    build_langgraph_agent's own `defer_tools`/`core_tool_names`
    parameters. Only applies to the top-level conversation loop --
    spawn_agent/spawn_agent_background's own sub-agent graphs already
    get an explicit, small, parent-chosen tool_names subset, so they
    have no version of this problem to solve.
    """

    wake_poll_seconds: int = 30
    """How often the web server checks for due sleep_until/sleep_for/
    wake_on/wake_on_event requests (tools/selfwake.py) and resumes their
    threads, while the server process is running -- see
    runtime_lg/selfwake.py's poll_due_wakes, run from web/app.py's
    lifespan. Has no effect on the CLI's --check-wakes flag, which is a
    single one-shot poll meant for external cron/systemd instead."""

    @field_validator("default_model")
    @classmethod
    def _validate_default_model(cls, value: str) -> str:
        if ":" not in value:
            raise ValueError(
                'default_model must be an aisuite "provider:model" string, '
                f'e.g. "anthropic:claude-sonnet-4-5" (got {value!r})'
            )
        return value

    @field_validator(
        "mcp_config_path",
        "hooks_config_path",
        "providers_config_path",
        "exec_policy_path",
        mode="before",
    )
    @classmethod
    def _blank_optional_path_is_unset(cls, value: object) -> object:
        # .env.example ships these two as present-but-blank (documenting the
        # variable without setting it); `cp .env.example .env` is the
        # documented setup step, so a blank string here is a real, common
        # case -- not just an edge case. Left alone, Path("") resolves to
        # Path(".") (the cwd), which then fails confusingly wherever the
        # path is opened as a file, rather than behaving like "unset".
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("extra_readable_dirs", "extra_writable_dirs", mode="before")
    @classmethod
    def _comma_separated_paths(cls, value: object) -> object:
        # pydantic-settings would otherwise expect a JSON array string for a
        # list field from the environment -- everywhere else in this file
        # uses plain unquoted .env values, so match that convention instead:
        # comma-separated, blank/unset = empty list.
        if isinstance(value, str):
            return [entry.strip() for entry in value.split(",") if entry.strip()]
        return value
