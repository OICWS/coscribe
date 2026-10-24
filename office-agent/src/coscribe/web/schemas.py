"""Request bodies of the server's routes."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


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


class DocxReviewEdit(BaseModel):
    path: str
    operations: list[dict[str, str]]


class DocBlocksSave(BaseModel):
    path: str
    # The file version the editor read: a save over a newer file is refused.
    version: str = Field(min_length=1, max_length=128)
    blocks: list[Any] = Field(max_length=20_000)


class DocRestore(BaseModel):
    path: str
    # The version the editor holds, and the kept version to put back over it.
    version: str = Field(min_length=1, max_length=128)
    to: str = Field(min_length=1, max_length=128)


class SlideCommentNew(BaseModel):
    path: str
    slide: str
    x: float
    y: float
    element: str
    text: str = Field(min_length=1, max_length=4000)
    sent: bool = False


class SlideCommentReply(BaseModel):
    path: str
    text: str = Field(min_length=1, max_length=4000)
    # The reply carries coscribe's answer to a comment sent to it, rather than the user's words.
    from_coscribe: bool = False


class SlideCommentState(BaseModel):
    path: str
    resolved: bool


class SlideSave(BaseModel):
    path: str
    source: str = Field(min_length=1, max_length=400_000)
    # The slide's version as the editor read it: a save over a newer file is refused.
    version: str = Field(min_length=1, max_length=128)


class SlideArrange(BaseModel):
    path: str
    action: Literal["insert", "duplicate", "move", "delete"]
    # The slide acted on; for "insert", the one the new slide follows (-1: first).
    index: int = Field(ge=-1, le=10_000)
    # "move": where the slide goes.
    to: int = Field(default=0, ge=0, le=10_000)
    # "insert": the new slide's section.
    source: str = Field(default="<section></section>", max_length=400_000)


class SlideHistoryStep(BaseModel):
    path: str
    action: Literal["undo", "redo", "discard"]


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


class ArtifactRename(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ArtifactShow(BaseModel):
    reveal: bool = False


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
    environment: str | None = None
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
