"""Request bodies of the server's routes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ConfigUpdate(BaseModel):
    updates: dict[str, str]


class BrowserHostReply(BaseModel):
    id: str
    ok: bool
    result: dict[str, Any] | None = None
    error: str | None = None


class SkillEnabledUpdate(BaseModel):
    enabled: bool


class OpenFileRequest(BaseModel):
    path: str
    reveal: bool = False


class MemoryUpdate(BaseModel):
    content: str


class MCPServerUpdate(BaseModel):
    name: str
    # Exactly one of command (local, stdio) or server_url (remote,
    # streamable_http) -- validate_mcp_config enforces the XOR, this model
    # just carries both possible shapes. headers is server_url's
    # equivalent of env, masked/secret-stored the same way on the read/
    # write paths below.
    command: str | None = None
    args: list[str] = []
    env: dict[str, str] = {}
    server_url: str | None = None
    headers: dict[str, str] = {}
    # "oauth": sign in through the server's own browser page; no headers.
    auth: str | None = None
    # With "oauth": the app registered with the service (see
    # runtime_lg/mcp_oauth.py) to sign in with, instead of a registration.
    oauth_app: str | None = None


class OAuthAppCredentials(BaseModel):
    client_id: str
    client_secret: str | None = None


class OAuthAppImport(BaseModel):
    apps: dict[str, OAuthAppCredentials]


class MCPVersionBump(BaseModel):
    package: str
    version: str


class ConnectorPermissionsUpdate(BaseModel):
    tools: dict[str, str]


class ThreadRename(BaseModel):
    title: str


class ThreadMetaPatch(BaseModel):
    # None leaves it; "" takes the conversation out of its group.
    group: str | None = None
    archived: bool | None = None


class GroupRename(BaseModel):
    name: str


class ScheduledTaskCreate(BaseModel):
    name: str
    kind: str
    at: str
    prompt: str = ""
    weekday: int | None = None
    day_of_month: int | None = None
    start_date: str | None = None
    model: str | None = None
    approval_mode: str = "manual"
    notes_enabled: bool = True
    workflow: dict[str, Any] | None = None
    workspace: str | None = None
    # The conversation draft this task was saved from, and that
    # conversation, if any.
    from_draft: str | None = None
    from_thread: str | None = None


class InvestigateRequest(BaseModel):
    # None: the default model.
    model: str | None = None


class RunNowRequest(BaseModel):
    inputs: dict[str, Any] | None = None


class WorkflowDraftRequest(BaseModel):
    name: str = ""


class WorkflowCheck(BaseModel):
    workflow: dict[str, Any]


class PermissionsRequest(BaseModel):
    workflow: dict[str, Any]
    # An earlier version, to say what this one adds.
    previous: dict[str, Any] | None = None
    # A saved task, whose runs may have been allowed more since.
    trigger_id: str | None = None
    # With a task: compare against its saved workflow.
    against_saved: bool = False


class WorkflowAnswer(BaseModel):
    approved: bool
    note: str = ""


class WorkflowRetry(BaseModel):
    # None: the step the run failed at (or, after a crash, wherever it stopped).
    step_id: str | None = None


class TaskNotesUpdate(BaseModel):
    notes: str




class ProviderUpdate(BaseModel):
    name: str
    # Blank for a built-in (anthropic/openai/gemini) -- their SDKs don't take
    # one, only custom OpenAI-compatible providers need it.
    base_url: str = ""
    api_key: str
    default_model: str = ""


class ScriptEnvPackageInstall(BaseModel):
    package: str


class ScriptEnvInterpreterUpdate(BaseModel):
    path: str  # blank clears the override, reverting to auto-detection
