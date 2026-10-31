"""A running Coscribe app, driven the way its page drives it: the REST routes for tasks and
runs, and a thread's WebSocket to follow an investigation and answer what it asks."""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import websockets

END_STATES = {"completed", "failed", "stopped", "needs_approval"}


@dataclass
class Followed:
    """What happened in an investigation: what it asked, what it proposed, its last word."""

    questions: list[str] = field(default_factory=list)
    approvals: list[str] = field(default_factory=list)
    revision: dict[str, Any] | None = None
    tests: list[str] = field(default_factory=list)
    reply: str = ""
    finished: bool = False


class App:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        page = httpx.get(self.base + "/", timeout=30).text
        found = re.search(r'name="coscribe-token" content="([^"]+)"', page)
        if not found:
            raise RuntimeError(f"No launch token on {self.base}/ -- is this a Coscribe app?")
        self.token = found.group(1)
        self.http = httpx.Client(
            base_url=self.base, headers={"x-coscribe-token": self.token}, timeout=60
        )

    def call(self, method: str, path: str, body: Any = None) -> Any:
        response = self.http.request(method, path, json=body if method != "GET" else None)
        response.raise_for_status()
        return response.json()

    def create_task(self, name: str, workflow: dict[str, Any]) -> str:
        body = {"name": name, "kind": "manual", "at": "", "workflow": workflow}
        task = self.call("POST", "/api/scheduled-tasks", body)
        return str(task["trigger_id"])

    def task(self, trigger_id: str) -> dict[str, Any]:
        tasks = self.call("GET", "/api/scheduled-tasks")
        tasks = tasks if isinstance(tasks, list) else tasks.get("tasks", [])
        return next(t for t in tasks if t["trigger_id"] == trigger_id)

    def set_workflow(self, trigger_id: str, workflow: dict[str, Any]) -> None:
        """What "Save changes" on a revision does."""
        t = self.task(trigger_id)
        self.call("PUT", f"/api/scheduled-tasks/{trigger_id}",
                  {"name": t["name"], "kind": "manual", "at": "", "workflow": workflow,
                   "environment": t.get("environment")})  # fmt: skip

    def delete_task(self, trigger_id: str) -> None:
        self.http.delete(f"/api/scheduled-tasks/{trigger_id}")

    def run(self, trigger_id: str, timeout: float) -> dict[str, Any]:
        run_id = self.call("POST", f"/api/scheduled-tasks/{trigger_id}/run")["run"]["run_id"]
        return self._until_ended(trigger_id, run_id, timeout)

    def decline(self, trigger_id: str, run_id: str, timeout: float) -> dict[str, Any]:
        """Say no to what a paused run asks, as a person would, and wait for it to end."""
        path = f"/api/scheduled-tasks/{trigger_id}/runs/{run_id}/answer"
        self.call("POST", path, {"approved": False, "note": ""})
        return self._until_ended(trigger_id, run_id, timeout)

    def _until_ended(self, trigger_id: str, run_id: str, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            run = next(r for r in self.task(trigger_id)["runs"] if r["run_id"] == run_id)
            if run["status"] in END_STATES:
                return dict(run)
            if time.monotonic() > deadline:
                self.http.post(f"/api/scheduled-tasks/{trigger_id}/runs/{run_id}/stop", json={})
                return {**run, "status": "timed_out"}
            time.sleep(2)

    def investigate(self, trigger_id: str, run_id: str, answer: str, timeout: float) -> Followed:
        path = f"/api/scheduled-tasks/{trigger_id}/runs/{run_id}/investigate"
        thread = self.call("POST", path, {})["thread_id"]
        return asyncio.run(self._follow(thread, answer, timeout))

    def _working(self, thread: str) -> bool:
        statuses = self.call("GET", "/api/threads/status").get("statuses", {})
        return statuses.get(thread) in ("working", "needs_input")

    async def _follow(self, thread: str, answer: str, timeout: float) -> Followed:
        """Approve what it asks to do, answer its questions with `answer`, and keep the last
        revision it drafted, until the conversation has been quiet for a while."""
        seen = Followed()
        ws_url = self.base.replace("http", "ws", 1) + f"/ws/{thread}?token={self.token}"
        deadline = time.monotonic() + timeout
        quiet_since: float | None = None
        async with websockets.connect(ws_url, max_size=None) as ws:
            while time.monotonic() < deadline:
                try:
                    event = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                except TimeoutError:
                    working = await asyncio.to_thread(self._working, thread)
                    if working:
                        quiet_since = None
                    elif quiet_since is None:
                        quiet_since = time.monotonic()
                    elif time.monotonic() - quiet_since > 10:
                        seen.finished = True
                        break
                    continue
                kind = event.get("type")
                if kind == "approval_required" and not event.get("subagent_id"):
                    seen.approvals.append(str(event.get("tool_name")))
                    await ws.send(json.dumps({"type": "approval_response", "id": event["id"],
                                              "approved": True}))  # fmt: skip
                elif kind == "question_required":
                    seen.questions.append(json.dumps(event.get("questions"), ensure_ascii=False))
                    answers = [answer] * len(event["questions"])
                    reply = {"type": "question_response", "id": event["id"], "answers": answers}
                    await ws.send(json.dumps(reply))
                elif kind == "tool_result":
                    self._note_result(seen, event.get("tool_name"), event.get("result"))
                elif kind == "history":
                    for entry in event.get("entries", []):
                        if entry.get("kind") == "tool":
                            self._note_result(seen, entry.get("tool_name"), entry.get("result"))
                elif kind == "agent_message":
                    seen.reply = str(event.get("text", ""))
        return seen

    @staticmethod
    def _note_result(seen: Followed, name: Any, result: Any) -> None:
        try:
            data = json.loads(result) if isinstance(result, str) else result
        except ValueError:
            return
        if not isinstance(data, dict):
            return
        if name == "revise_workflow" and data.get("status") == "drafted":
            seen.revision = data
        elif name == "test_workflow":
            seen.tests.append(str(data.get("status") or data.get("run", {}).get("status")))
