"""A stand-in for `codex app-server` for the code module's tests.

Speaks the same newline-delimited JSON-RPC, with message shapes taken from
a real 0.160.0 session. The prompt's first word picks what the turn does:

    basic      streams a reply and token usage
    approve    asks to run a command; on accept writes made.txt, else declines
    read       the same, for `ls -la`
    readout    the same, for `cat ../x.txt`
    file       asks to apply a file change
    hang       runs until turn/interrupt, sending nothing
    slowcmd    runs a command that takes 1.5 s and prints nothing
    child      starts a long-lived child process (pid in child.pid), then hangs;
               as in Codex, an interrupt leaves it running and
               thread/backgroundTerminals/clean ends it
    crash      exits mid-turn
    fail       ends the turn as failed
    tool       calls a host tool nobody registered
    early      sends the turn's events before answering turn/start
    count      replies with how many turns the thread has had

As in Codex, a thread can be resumed once a turn of it has run, also by a
later process: those threads are kept in $CODEX_HOME/fake_threads.json.
Every request it receives is appended to $FAKE_CODEX_LOG as JSON. With
$FAKE_CODEX_NO_INIT set it never answers initialize.
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

_write_lock = threading.Lock()
_replies: dict[Any, dict[str, Any]] = {}
_reply_ready = threading.Condition()
_interrupted: dict[str, threading.Event] = {}
_ids = itertools.count(1000)
_threads: dict[str, str] = {}
_turns: dict[str, int] = {}
_children: dict[str, list[subprocess.Popen[bytes]]] = {}


def _kept_path() -> Path | None:
    home = os.environ.get("CODEX_HOME")
    return Path(home, "fake_threads.json") if home else None


def _kept() -> dict[str, list[Any]]:
    path = _kept_path()
    if path is None or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _keep(thread_id: str) -> None:
    path = _kept_path()
    if path is None:
        return
    kept = _kept()
    kept[thread_id] = [_threads[thread_id], _turns[thread_id]]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(kept), encoding="utf-8")


def send(message: dict[str, Any]) -> None:
    with _write_lock:
        sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def notify(method: str, params: dict[str, Any]) -> None:
    send({"method": method, "params": params})


def ask(method: str, params: dict[str, Any]) -> dict[str, Any]:
    request_id = next(_ids)
    send({"id": request_id, "method": method, "params": params})
    with _reply_ready:
        while request_id not in _replies:
            _reply_ready.wait()
        return _replies.pop(request_id)


def usage(thread_id: str, turn_id: str, cached: int) -> None:
    last = {
        "totalTokens": 1200,
        "inputTokens": 1000,
        "cachedInputTokens": cached,
        "cacheWriteInputTokens": 0,
        "outputTokens": 200,
        "reasoningOutputTokens": 20,
    }
    notify(
        "thread/tokenUsage/updated",
        {
            "threadId": thread_id,
            "turnId": turn_id,
            "tokenUsage": {"total": last, "last": last, "modelContextWindow": None},
        },
    )


def message(thread_id: str, turn_id: str, text: str) -> None:
    item_id = f"msg-{next(_ids)}"
    base = {"threadId": thread_id, "turnId": turn_id}
    notify("item/started", {**base, "item": {"type": "agentMessage", "id": item_id, "text": ""}})
    for piece in (text[: len(text) // 2], text[len(text) // 2 :]):
        notify("item/agentMessage/delta", {**base, "itemId": item_id, "delta": piece})
    notify(
        "item/completed", {**base, "item": {"type": "agentMessage", "id": item_id, "text": text}}
    )


def complete(thread_id: str, turn_id: str, status: str, error: str | None = None) -> None:
    notify(
        "turn/completed",
        {
            "threadId": thread_id,
            "turn": {
                "id": turn_id,
                "items": [],
                "status": status,
                "error": {"message": error} if error else None,
            },
        },
    )


def command(
    thread_id: str,
    turn_id: str,
    cwd: str,
    script: str = "echo made > made.txt",
    call: str = "call-1",
    out: str = "made.txt",
) -> None:
    base = {"threadId": thread_id, "turnId": turn_id}
    item = {"type": "commandExecution", "id": call, "command": f"/bin/bash -lc '{script}'"}
    notify("item/started", {**base, "item": {**item, "status": "inProgress"}})
    reply = ask(
        "item/commandExecution/requestApproval",
        {
            **base,
            "kind": "command",
            "itemId": call,
            "startedAtMs": 0,
            "environmentId": "local",
            "command": item["command"],
            "cwd": cwd,
            "reason": "writes made.txt",
            "commandActions": [{"type": "unknown", "command": script}],
        },
    )
    accepted = reply.get("result", {}).get("decision") == "accept"
    if accepted:
        Path(cwd, out).write_text("made\n", encoding="utf-8")
    status = "completed" if accepted else "declined"
    notify("item/completed", {**base, "item": {**item, "status": status, "exitCode": 0}})
    message(thread_id, turn_id, "ran it" if accepted else "was declined")


def run_turn(thread_id: str, turn_id: str, prompt: str, cwd: str) -> None:
    scenario = prompt.split()[0] if prompt.split() else "basic"
    base = {"threadId": thread_id, "turnId": turn_id}
    _turns[thread_id] = _turns.get(thread_id, 0) + 1
    _keep(thread_id)
    if scenario == "approve":
        command(thread_id, turn_id, cwd)
    elif scenario == "twice":
        command(thread_id, turn_id, cwd)
        command(thread_id, turn_id, cwd, "echo more > made2.txt", "call-2", "made2.txt")
    elif scenario == "read":
        command(thread_id, turn_id, cwd, "ls -la")
    elif scenario == "readout":
        command(thread_id, turn_id, cwd, "cat ../x.txt")
    elif scenario == "file":
        item = {"type": "fileChange", "id": "patch-1", "changes": [{"path": "notes.md"}]}
        notify("item/started", {**base, "item": {**item, "status": "inProgress"}})
        reply = ask(
            "item/fileChange/requestApproval",
            {**base, "itemId": "patch-1", "startedAtMs": 0, "reason": "edit notes"},
        )
        accepted = reply.get("result", {}).get("decision") == "accept"
        if accepted:
            Path(cwd, "notes.md").write_text("notes\n", encoding="utf-8")
        status = "completed" if accepted else "declined"
        notify("item/completed", {**base, "item": {**item, "status": status}})
        message(thread_id, turn_id, "patched")
    elif scenario in ("hang", "child"):
        if scenario == "child":
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
            _children.setdefault(thread_id, []).append(child)
            Path(cwd, "child.pid").write_text(str(child.pid), encoding="utf-8")
        _interrupted[turn_id].wait()
        complete(thread_id, turn_id, "interrupted")
        return
    elif scenario == "slowcmd":
        item = {"type": "commandExecution", "id": "call-slow", "command": "sleep 1.5"}
        notify("item/started", {**base, "item": {**item, "status": "inProgress"}})
        time.sleep(1.5)
        notify("item/completed", {**base, "item": {**item, "status": "completed", "exitCode": 0}})
        message(thread_id, turn_id, "slept")
    elif scenario == "crash":
        message(thread_id, turn_id, "about to crash")
        os._exit(3)
    elif scenario == "fail":
        notify("error", {**base, "error": {"message": "retrying"}, "willRetry": True})
        notify("error", {**base, "error": {"message": "model unavailable"}, "willRetry": False})
        complete(thread_id, turn_id, "failed", "model unavailable")
        return
    elif scenario == "tool":
        reply = ask(
            "item/tool/call",
            {**base, "callId": "c1", "tool": "read_xlsx_summary", "arguments": {}},
        )
        message(thread_id, turn_id, json.dumps(reply.get("error", {}).get("code")))
    elif scenario == "count":
        message(thread_id, turn_id, f"turn {_turns[thread_id]}")
    else:
        message(thread_id, turn_id, "Hello from Codex")
    usage(thread_id, turn_id, cached=800)
    complete(thread_id, turn_id, "completed")


def handle(request: dict[str, Any]) -> None:
    method, params, request_id = request["method"], request.get("params") or {}, request["id"]
    if method == "initialize":
        if os.environ.get("FAKE_CODEX_NO_INIT"):
            return
        send({"id": request_id, "result": {"userAgent": "fake-codex/0.160.0"}})
    elif method == "thread/start":
        thread_id = f"thr-{next(_ids)}"
        _threads[thread_id] = params.get("cwd") or os.getcwd()
        send({"id": request_id, "result": {"thread": {"id": thread_id}}})
    elif method == "thread/resume":
        thread_id = params["threadId"]
        kept = _kept().get(thread_id)
        if thread_id not in _turns and kept is None:
            message_text = f"no rollout found for thread id {thread_id}"
            send({"id": request_id, "error": {"code": -32600, "message": message_text}})
            return
        if kept is not None and thread_id not in _turns:
            _threads[thread_id], _turns[thread_id] = kept
        _threads[thread_id] = params.get("cwd") or _threads[thread_id]
        send({"id": request_id, "result": {"thread": {"id": thread_id}}})
    elif method == "turn/start":
        thread_id = params["threadId"]
        turn_id = f"turn-{next(_ids)}"
        _interrupted[turn_id] = threading.Event()
        prompt = params["input"][0]["text"]
        worker = threading.Thread(
            target=run_turn, args=(thread_id, turn_id, prompt, _threads[thread_id]), daemon=True
        )
        if prompt.startswith("early"):
            message(thread_id, turn_id, "early bird")
            usage(thread_id, turn_id, cached=0)
            complete(thread_id, turn_id, "completed")
            time.sleep(0.05)
            send({"id": request_id, "result": {"turn": {"id": turn_id, "status": "inProgress"}}})
            return
        send({"id": request_id, "result": {"turn": {"id": turn_id, "status": "inProgress"}}})
        worker.start()
    elif method == "thread/backgroundTerminals/clean":
        for child in _children.pop(params["threadId"], []):
            child.kill()
            child.wait()
        send({"id": request_id, "result": {}})
    elif method == "turn/interrupt":
        _interrupted[params["turnId"]].set()
        send({"id": request_id, "result": {}})
    else:
        send({"id": request_id, "error": {"code": -32601, "message": f"unknown {method}"}})


def main() -> None:
    log_path = os.environ.get("FAKE_CODEX_LOG")
    for line in sys.stdin:
        message_in = json.loads(line)
        if log_path:
            with open(log_path, "a", encoding="utf-8") as log:
                log.write(json.dumps(message_in) + "\n")
        if "method" in message_in and "id" in message_in:
            threading.Thread(target=handle, args=(message_in,), daemon=True).start()
        elif "id" in message_in:
            with _reply_ready:
                _replies[message_in["id"]] = message_in
                _reply_ready.notify_all()


if __name__ == "__main__":
    main()
