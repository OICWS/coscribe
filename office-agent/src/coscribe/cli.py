"""Interactive CLI: the first, UI-less way to talk to the Coordinator
agent -- on runtime_lg (LangChain/LangGraph-based), promoted here to
replace the original hand-rolled-runtime implementation (see
runtime_lg/README.md's "coscribe-lg reaches real feature parity"
section for how this got here, and its Context section for why the
migration happened at all: the original runtime's vendored Gemini adapter
shipped three real bugs in a row).

Reuses `web/session.py`'s `ChatSessionLG` directly instead of
re-implementing /plan, /accept-edits, /compact, /clear, and the workflow
commands a second time -- every one of those is already built and tested
against the web transport, and `ChatSessionLG`'s constructor needs
nothing web-specific (settings, a thread id, a checkpointer, tools --
all things this CLI already has to build anyway). `_CliSocket` below is
a small duck-typed stand-in for the
`WebSocket` parameter `ChatSessionLG`'s methods expect -- it only ever gets
`.send_json(...)` called on it, which this class renders to the terminal
instead of over a real socket.

One deliberate behavior difference from `_CliSocket`'s web counterpart: for
`approval_required`, it prompts and resolves the decision synchronously,
right there inside `send_json`, before returning -- safe because a CLI has
no second concurrent client the way a browser tab reconnecting mid-approval
does (`ChatSessionLG._decide_action_request` registers the pending Future
*before* awaiting `send_json`, so resolving it inside that same call, before
control ever reaches `await future`, just makes the await return
immediately -- no deadlock). `app.py`'s `ws_endpoint` instead has to wait
for a separate `approval_response` message to arrive over the wire later,
precisely because a real browser client can't answer synchronously inside
that same call.

Another deliberate simplification: unlike this command's own pre-promotion
`--message` mode (which never interpreted slash-commands, "sent as-is to
the agent"), `--message` here goes through the same `handle_user_message`
every other turn does, so a one-shot message that happens to start with
`/plan` etc. is now interpreted as that command rather than sent verbatim.
This was a narrow, easy-to-avoid edge case (a scheduled job's message
starting with "/"); trading it for one shared code path instead of a
second, slightly-different one is worth it.

Auto-compact-over-threshold (the pre-promotion CLI's `maybe_auto_compact`)
is NOT ported here, and not silently missing either -- deliberately left
out because the thing it depends on doesn't exist yet in this runtime:
`session.py`'s own module comments already record that Gemini's
streaming path returns all-zero `usage_metadata`, so there is no reliable
token-usage signal to trigger an automatic compaction on. Manual `/compact`
works fully; wiring up a real usage signal (and porting auto-compact to
both this CLI and the web session) is separate, future work, not
something to fake here.
"""

# ruff: noqa: E402 -- INIT_PROMPT/_load_settings are defined below,
# *before* `from .web.session import ChatSessionLG` further down, on
# purpose: session.py itself does `from ..cli import INIT_PROMPT`, so
# importing it any earlier in this file (before that name exists as an
# attribute on this partially-initialized module) is a real circular
# import (confirmed the hard way -- ImportError: cannot import name
# 'INIT_PROMPT' from partially initialized module 'coscribe.cli').
# Python resolves this fine as long as the name is already bound by the
# time the circular back-import happens, which this ordering guarantees.
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import typer
from dotenv import load_dotenv
from pydantic import ValidationError

from .config import Settings
from .runtime import (
    LLMClient,
    empty_hooks_config,
    load_hooks_config,
    resolve_env_keyring_refs,
    run_hook,
)
from .runtime.provider_config import load_custom_providers
from .runtime_lg import poll_due_scheduled_tasks, poll_due_wakes
from .runtime_lg.mcp import connect_mcp_tools_lg
from .tools import load_builtin_skills, load_skills
from .tools.workflows import reconcile_interrupted_runs

app = typer.Typer(add_completion=False, no_args_is_help=False)
logger = logging.getLogger(__name__)

INIT_PROMPT = (
    "Explore the workspace directory with your file tools (list/read/search) "
    "and write a project overview to OVERVIEW.md at its root, covering: what "
    "the workspace contains, its structure, key files, and anything a future "
    "session of this assistant should know before working in it. Keep it "
    "concise. If OVERVIEW.md already exists, refresh it rather than "
    "duplicating content."
)


def _app_data_dir() -> Path:
    """Per-user application-data directory -- the fallback _dotenv_path
    below uses when the process has no meaningful project-directory cwd
    (office-agent-desktop's Tauri shell spawns the sidecar with no
    natural project dir to look for a `.env` in, and the same is true
    running the PyInstaller-packaged binary directly). Same convention
    most desktop apps use: `%APPDATA%\\coscribe` on Windows,
    `~/Library/Application Support/coscribe` on macOS,
    `~/.config/coscribe` elsewhere."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        return Path(base) / "coscribe" if base else Path.home() / "coscribe"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "coscribe"
    return Path.home() / ".config" / "coscribe"


def _dotenv_path() -> Path:
    """Where to load/write `.env`. Prefers a real, existing project-
    relative `./.env` -- the ordinary CLI/manually-run-web-server case,
    completely unchanged from before -- and only falls back to
    _app_data_dir() (creating it if needed) when no such file exists,
    which is the normal case for office-agent-desktop's sidecar (no
    natural project cwd) or the packaged binary run directly. The Tauri
    shell additionally spawns the sidecar with this same directory as
    its cwd (belt-and-suspenders, not required for this to work) -- so
    this fallback mainly matters for running the packaged binary on its
    own, e.g. this project's own PyInstaller smoke test."""
    cwd_env = Path(".env")
    if cwd_env.exists():
        return cwd_env
    app_data_env = _app_data_dir() / ".env"
    app_data_env.parent.mkdir(parents=True, exist_ok=True)
    return app_data_env


def _prepare_env() -> Path:
    """Shared first half of _load_settings/_load_settings_or_none below:
    resolves .env's real location and loads it into the real process
    environment, so both callers agree on the exact same file (see
    _dotenv_path's own docstring for why that matters) and both get a
    real provider API key rather than a stale keyring-ref sentinel.

    Settings' own env_file only feeds its declared COSCRIBE_-prefixed
    fields; provider API keys (GEMINI_API_KEY etc.) are unprefixed and
    read directly from os.environ by each provider SDK, so .env needs to
    be loaded into the real process environment too, not just parsed by
    Settings -- see config.py's Settings docstring.
    """
    dotenv_path = _dotenv_path()
    # override=True: .env is the one place a double-click desktop app's
    # user can configure anything at all (no terminal to set a real
    # environment variable in) -- live-hit without this: a corporate
    # machine already had HTTP_PROXY/HTTPS_PROXY set at the Windows
    # user/system level (a security agent's local traffic-interception
    # relay, pointed at 127.0.0.1 rather than the real upstream proxy),
    # and python-dotenv's own default (override=False) silently kept
    # that stale value instead of the one the user had just written into
    # .env, with no error -- the Gemini call just failed to reach the
    # real proxy at all.
    load_dotenv(dotenv_path, override=True)
    # A provider API key written by a previous run may be a keyring-ref
    # sentinel string rather than the real value (see runtime/secrets.py)
    # -- resolve it in the real process environment right here, the same
    # place .env just got loaded into it, so every downstream
    # os.getenv("ANTHROPIC_API_KEY")-style read (inside the SDKs
    # themselves) sees the real secret, never the sentinel.
    resolve_env_keyring_refs()
    return dotenv_path


def _load_settings() -> Settings:
    dotenv_path = _prepare_env()
    try:
        return Settings(_env_file=dotenv_path)  # type: ignore[call-arg]
    except ValidationError as exc:
        typer.echo("Configuration error:\n", err=True)
        typer.echo(str(exc), err=True)
        typer.echo(
            "\nCopy .env.example to .env and set COSCRIBE_DEFAULT_MODEL "
            "(and the API key for whichever provider you use).",
            err=True,
        )
        raise typer.Exit(code=1) from exc


def _load_settings_or_none() -> Settings | None:
    """Like _load_settings, but returns None instead of printing an error
    and exiting the process on a config ValidationError -- web/app.py's
    main() uses this to fall into first-run setup mode (create_setup_app)
    rather than crashing outright the moment a fresh install has no
    default_model/API key configured yet. That crash used to be the whole
    story for the desktop app: office-agent-desktop's splash page has no
    way to tell it apart from any other startup failure, so it just showed
    "coscribe couldn't start" and pointed the user at a log file to hand-
    edit .env from (see office-agent-desktop/dist/index.html's own
    showFailure). _load_settings above is unchanged and still used by the
    CLI: a terminal is already the right place to fix a bad .env by hand,
    no setup UI needed there."""
    dotenv_path = _prepare_env()
    try:
        return Settings(_env_file=dotenv_path)  # type: ignore[call-arg]
    except ValidationError:
        return None


from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # noqa: E402

from .web.session import ChatSessionLG  # noqa: E402


class _CliSocket:
    """Duck-typed WebSocket stand-in -- see this module's docstring."""

    def __init__(self, session: ChatSessionLG) -> None:
        self._session = session
        self._streamed = ""
        self.had_error = False

    async def send_json(self, data: dict[str, Any]) -> None:
        kind = data["type"]
        if kind == "agent_delta":
            typer.echo(data["text"], nl=False)
            self._streamed += data["text"]
        elif kind == "agent_message":
            # Only print what wasn't already streamed via agent_delta above
            # -- mirrors the old cli.py's identical diff-against-final-text
            # logic, since _format_reply can append a note (empty reply,
            # turn limit) that never streamed.
            text = data["text"]
            if not self._streamed:
                typer.echo(text)
            elif text != self._streamed:
                typer.echo(text[len(self._streamed) :])
            else:
                typer.echo()
            self._streamed = ""
        elif kind == "tool_result":
            typer.echo(f"\n  ran {data['tool_name']}")
        elif kind == "approval_required":
            typer.echo(f"\n[approval required] {data['tool_name']}({data['arguments']})")
            approved = typer.confirm("Allow this action?", default=False)
            self._session.resolve_approval(data["id"], approved)
        elif kind == "history":
            for entry in data["entries"]:
                if entry["kind"] == "user":
                    typer.echo(f"you: {entry['text']}")
                elif entry["kind"] == "agent":
                    typer.echo(f"agent: {entry['text']}")
                elif entry["kind"] == "tool":
                    typer.echo(f"  ran {entry['tool_name']}")
        elif kind == "error":
            self.had_error = True
            typer.echo(f"\nerror: {data['message']}\n", err=True)
        elif kind == "compacted":
            typer.echo(f"Compacted {data['before']} messages down to {data['after']}.\n")
        elif kind == "cleared":
            typer.echo("Cleared this thread's conversation history.\n")
        elif kind == "recording_started":
            note = (
                " (discarded a previous in-progress recording)"
                if data.get("discarded_previous")
                else ""
            )
            typer.echo(
                f"Recording started{note}. Perform the steps, then /endworkflow <name>.\n"
            )
        elif kind == "workflow_saved":
            typer.echo(f"Saved workflow {data['name']!r} ({data['mode']}).\n")
        elif kind == "workflow_run_started":
            typer.echo(f"Running workflow {data['name']!r}...")
        elif kind == "workflow_run_progress":
            run = data["run"]
            steps = run.get("steps") or []
            running = next((s for s in steps if s.get("status") == "running"), None)
            if running is not None:
                step_label = f"{running['index']}: {running['tool_name']}"
                typer.echo(f"  [{run.get('status')}] step {step_label}")
            else:
                typer.echo(f"  [{run.get('status')}]")
        # "state"/"tasks_changed": no terminal equivalent needed -- state is
        # printed once explicitly at startup (see chat() below), and there's
        # no persistent task panel to refresh in a REPL.


@app.command()
def chat(
    thread: str | None = typer.Option(
        None, "--thread", help="Resume a previous conversation by thread id."
    ),
    message: str | None = typer.Option(
        None,
        "--message",
        "-m",
        help="Send one message non-interactively and exit, instead of starting an "
        "interactive session -- for cron/systemd. Combine with --accept-edits for a "
        "fully unattended run -- without it, a tool call that requires approval will "
        "hang waiting on a prompt with no one there to answer it.",
    ),
    accept_edits: bool = typer.Option(
        False,
        "--accept-edits",
        help="Start in accept-edits mode (no approval prompts) instead of only "
        "reaching it interactively via /accept-edits.",
    ),
    check_wakes: bool = typer.Option(
        False,
        "--check-wakes",
        help="Resume every thread with a currently-due sleep_until/sleep_for/"
        "wake_on/wake_on_event request, run every due Scheduled Task, print "
        "a one-line summary of what fired, and exit -- instead of starting "
        "a chat session. Meant to be "
        "scheduled on a tight interval (e.g. every 5 minutes) via external "
        "cron/systemd for a setup that doesn't keep `coscribe-web` running; "
        "the web server already does this automatically (see "
        "COSCRIBE_WAKE_POLL_SECONDS) while it's up, so this flag is only "
        "needed alongside a pure-CLI setup. Ignores --thread/--message/"
        "--accept-edits, since it may resume many different threads in one "
        "run, each with its own already-saved skills.",
    ),
) -> None:
    """Start an interactive chat session on the LangGraph-based runtime."""
    settings = _load_settings()
    logging.basicConfig(level=settings.log_level)
    if check_wakes:
        asyncio.run(_check_wakes_async(settings))
        return
    asyncio.run(_chat_async(settings, thread, message, accept_edits))


async def _chat_async(
    settings: Any,
    thread: str | None,
    message: str | None,
    accept_edits: bool,
) -> None:
    interrupted = reconcile_interrupted_runs(settings.state_dir)
    if interrupted:
        logger.warning(
            "Marked %d workflow run(s) as failed -- still 'running' at startup, "
            "left over from a previous process that didn't shut down cleanly.",
            interrupted,
        )

    thread_id = thread or uuid.uuid4().hex[:8]

    hooks_config: dict[str, list[str]] = empty_hooks_config()
    if settings.hooks_config_path is not None:
        hooks_config = load_hooks_config(settings.hooks_config_path)
        session_start_payload = {
            "event": "SessionStart",
            "agent_name": "coordinator",
            "thread_id": thread_id,
        }
        for command in hooks_config["SessionStart"]:
            session_start_result = run_hook(command, session_start_payload)
            if not session_start_result.allowed:
                logger.warning("SessionStart hook failed: %s", session_start_result.reason)

    custom_providers: dict[str, dict[str, str]] = {}
    if settings.providers_config_path is not None:
        custom_providers = load_custom_providers(settings.providers_config_path)
    context_window_client = LLMClient(custom_providers=custom_providers)

    mcp_tools: list[Any] = []
    mcp_connections: dict[str, Any] = {}
    if settings.mcp_config_path is not None:
        mcp_tools, mcp_connections = await connect_mcp_tools_lg(settings.mcp_config_path)
        if mcp_tools:
            typer.echo(f"Loaded {len(mcp_tools)} MCP tool(s).\n")

    checkpoint_path = settings.state_dir / "runtime_lg_checkpoints.sqlite"
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
            session = ChatSessionLG(
                thread_id=thread_id,
                settings=settings,
                context_window_client=context_window_client,
                custom_providers=custom_providers,
                extra_tools=mcp_tools,
                checkpointer=checkpointer,
                hooks_config=hooks_config,
                # Matches web/app.py's default_enabled_skill_names -- without
                # this, ChatSessionLG's own default (an empty set, not None)
                # means the 3 built-in skills silently never get offered to
                # the CLI at all (confirmed live: a real DeepSeek run's
                # load_skill("PPTX Slides") call had no tool to call).
                enabled_skill_names={s.name for s in load_builtin_skills()},
            )
            if accept_edits:
                session.accept_edits = True
            socket = _CliSocket(session)

            if message is not None:
                await session.handle_user_message(message, socket)  # type: ignore[arg-type]
                if socket.had_error:
                    raise typer.Exit(code=1)
                return

            typer.echo(
                f"coscribe -- thread '{thread_id}'. Type 'exit' to quit, "
                "/plan to toggle plan mode, /accept-edits to toggle auto-accept, "
                "/compact to summarize this thread down and reclaim context, "
                "/clear to wipe this thread's history and start fresh, "
                "/startworkflow and /endworkflow <name> to record a chain "
                "workflow precisely, /saveworkflow <name> to have the model figure "
                "out what to save from this conversation, /runworkflow <name> to run "
                "a saved workflow, /init to write a project overview, or /<skill-name> "
                "to invoke a skill directly.\n"
            )

            # A pending approval or real conversation history from a
            # previous process (this same --thread resumed) -- redeliver/
            # replay both before prompting, same as a fresh WS connection.
            await session.send_history(socket)  # type: ignore[arg-type]
            await session.resume_after_reconnect(socket)  # type: ignore[arg-type]

            while True:
                mode_flags = (("plan", session.plan_mode), ("accept-edits", session.accept_edits))
                active_modes = [label for label, on in mode_flags if on]
                prompt_label = f"you [{'+'.join(active_modes)}]" if active_modes else "you"
                try:
                    user_input = typer.prompt(prompt_label)
                except (KeyboardInterrupt, EOFError):
                    typer.echo("\nbye.")
                    raise typer.Exit() from None

                stripped_lower = user_input.strip().lower()
                if stripped_lower in {"exit", "quit"}:
                    typer.echo("bye.")
                    raise typer.Exit()

                # Every other command -- /plan, /accept-edits, /compact,
                # /clear, /startworkflow, /endworkflow, /saveworkflow,
                # /runworkflow, /init, /<skill-name> -- is recognized and
                # handled entirely inside handle_user_message itself (see
                # _handle_user_message_locked); nothing left for this loop
                # to pre-parse.
                await session.handle_user_message(user_input, socket)  # type: ignore[arg-type]
                typer.echo()
    finally:
        for connection in mcp_connections.values():
            await connection.close()


async def _check_wakes_async(settings: Any) -> None:
    """`coscribe --check-wakes`: a single one-shot poll for external cron/
    systemd, the CLI-only counterpart to web/app.py's always-on background
    poll loop -- checks both runtime_lg/selfwake.py's poll_due_wakes and
    runtime_lg/scheduled_tasks.py's poll_due_scheduled_tasks in the same
    pass (one cron entry covers both kinds of "check for due things," not
    a second flag to configure), both callers sharing the same
    get_session with a different resolver function each.

    Unlike _chat_async, this may resume many different threads in one run
    (whichever have a currently-due wake), each of which already has its
    own enabled-skills choice saved from whenever it was last used
    interactively -- so this reads each thread's own `.skills` sidecar
    file, mirroring web/app.py's _resolve_enabled_skills exactly.
    Duplicated rather than imported from there since that's a closure
    private to create_app -- consistent with the existing amount of
    construction-logic duplication between this module and web/app.py
    (see _chat_async's own docstring-adjacent comments)."""
    skills_by_name = {
        s.name: s for s in load_builtin_skills() + load_skills(settings.skills_dir)
    }
    default_enabled_skill_names = {s.name for s in load_builtin_skills()}

    def _resolve_enabled_skills(thread_id: str) -> set[str]:
        sidecar = settings.state_dir / f"{thread_id}.skills"
        if not sidecar.is_file():
            return set(default_enabled_skill_names)
        try:
            saved = json.loads(sidecar.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return set(default_enabled_skill_names)
        return set(saved) & skills_by_name.keys()

    hooks_config: dict[str, list[str]] = empty_hooks_config()
    if settings.hooks_config_path is not None:
        hooks_config = load_hooks_config(settings.hooks_config_path)

    custom_providers: dict[str, dict[str, str]] = {}
    if settings.providers_config_path is not None:
        custom_providers = load_custom_providers(settings.providers_config_path)
    context_window_client = LLMClient(custom_providers=custom_providers)

    mcp_tools: list[Any] = []
    mcp_connections: dict[str, Any] = {}
    if settings.mcp_config_path is not None:
        mcp_tools, mcp_connections = await connect_mcp_tools_lg(settings.mcp_config_path)

    checkpoint_path = settings.state_dir / "runtime_lg_checkpoints.sqlite"
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:

            async def _get_session(thread_id: str) -> ChatSessionLG:
                return ChatSessionLG(
                    thread_id=thread_id,
                    settings=settings,
                    context_window_client=context_window_client,
                    custom_providers=custom_providers,
                    extra_tools=mcp_tools,
                    checkpointer=checkpointer,
                    hooks_config=hooks_config,
                    enabled_skill_names=_resolve_enabled_skills(thread_id),
                )

            fired_wakes = await poll_due_wakes(settings.state_dir, _get_session)
            fired_tasks = await poll_due_scheduled_tasks(settings.state_dir, _get_session)
    finally:
        for connection in mcp_connections.values():
            await connection.close()

    if not fired_wakes and not fired_tasks:
        typer.echo("No due wakes.")
        return
    for wake in fired_wakes:
        typer.echo(f"Resumed thread {wake.thread_id!r} ({wake.kind}): {wake.reason}")
    for trigger in fired_tasks:
        typer.echo(f"Ran scheduled task {trigger.name!r} ({trigger.trigger_id})")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
