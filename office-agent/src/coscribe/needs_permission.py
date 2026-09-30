"""Something a saved workflow's run was stopped from doing because the
person hasn't allowed it yet: a folder outside its own, or a site its
steps don't name. A run that may ask waits for an answer; anywhere else
this is an ordinary refusal with a message that says what to allow."""

from __future__ import annotations

from typing import Any, Literal

SITE_MARK = "NEEDS_SITE_PERMISSION:"


class NeedsPermission(PermissionError):
    """`target` is what to allow: a folder to add, or a site's host."""

    def __init__(self, kind: Literal["folder", "site"], target: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.target = target
        self.message = message

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "target": self.target, "message": self.message}
