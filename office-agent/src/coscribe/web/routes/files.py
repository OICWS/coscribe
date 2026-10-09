"""Files routes."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Form,
    UploadFile,
)
from fastapi.responses import FileResponse, JSONResponse, Response

from ...tools._workspace import WorkspaceScope
from ...tools.browser import SCREENSHOT_FOLDER
from ...tools.pdf_pages import pdf_page_count, render_pdf_page
from ..state import AppState
from .shared import MAX_UPLOAD_BYTES

_PREVIEW_NAME_RE = re.compile(r"[0-9a-f]{32}\.png")  # tools/_thumbnail.py's uuid4().hex naming


def _unique_upload_path(scope: WorkspaceScope, filename: str) -> Path:
    name = Path(filename).name or "upload"
    candidate = scope.resolve(name)
    if not candidate.exists():
        return candidate
    stem, suffix, counter = candidate.stem, candidate.suffix, 1
    while True:
        candidate = scope.resolve(f"{stem} ({counter}){suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def router(state: AppState) -> APIRouter:
    router = APIRouter()
    settings = state.settings
    _get_session = state.get_session


    @router.post("/api/upload")
    async def upload_file(
        file: UploadFile, thread_id: Annotated[str, Form()] = ""
    ) -> JSONResponse:
        # Direct port of web/app.py's identical endpoint -- pure file I/O
        # against settings.workspace_root, nothing runtime-specific about
        # it (unlike /api/config, /api/mcp/*, /api/providers/*, this one
        # has no "next new session only" wrinkle: an uploaded file just
        # needs to exist on disk before the model's next tool call reads
        # it, which every already-open ChatSessionLG can do immediately).
        content = await file.read()
        if len(content) > MAX_UPLOAD_BYTES:
            return JSONResponse({"error": "file too large (max 25MB)"}, status_code=413)
        # Into the conversation's own folder when it has one: the model
        # reads the path relative to that, not to the app's default.
        scope = (
            _get_session(thread_id).workspace_scope()
            if thread_id
            else WorkspaceScope(settings.workspace_root)
        )
        path = _unique_upload_path(scope, file.filename or "upload")
        path.write_bytes(content)
        return JSONResponse({"path": scope.relative(path), "bytes_written": len(content)})

    def _attachment_pdf(path: str, thread_id: str) -> Path | JSONResponse:
        scope = (
            _get_session(thread_id).workspace_scope()
            if thread_id
            else WorkspaceScope(settings.workspace_root)
        )
        try:
            file_path = scope.resolve(path)
        except PermissionError:
            return JSONResponse({"error": "Path is outside the workspace."}, status_code=400)
        if file_path.suffix.lower() != ".pdf" or not file_path.is_file():
            return JSONResponse({"error": "No such PDF."}, status_code=404)
        return file_path

    @router.get("/api/attachment/pdf")
    async def get_attachment_pdf_info(path: str, thread_id: str = "") -> JSONResponse:
        """How many pages an attached PDF has, for the preview dialog."""
        found = _attachment_pdf(path, thread_id)
        if isinstance(found, JSONResponse):
            return found
        try:
            return JSONResponse({"pages": await asyncio.to_thread(pdf_page_count, found)})
        except Exception:  # noqa: BLE001 -- a PDF PDFium can't open is shown as such
            return JSONResponse({"error": "This PDF can't be opened."}, status_code=422)

    @router.get("/api/attachment/pdf/page")
    async def get_attachment_pdf_page(
        path: str, page: int = 1, width: int = 600, thread_id: str = ""
    ) -> Response:
        found = _attachment_pdf(path, thread_id)
        if isinstance(found, JSONResponse):
            return found
        try:
            png = await asyncio.to_thread(render_pdf_page, found, page, width)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except Exception:  # noqa: BLE001 -- see above
            return JSONResponse({"error": "This PDF can't be opened."}, status_code=422)
        return Response(png, media_type="image/png", headers={"Cache-Control": "max-age=3600"})

    @router.get("/api/previews/{name}")
    async def get_preview(name: str) -> Response:
        # Serves the write_docx/write_xlsx/write_pptx thumbnails written by
        # tools/_thumbnail.py's render_thumbnail under
        # settings.state_dir/previews/ -- not a general file-access endpoint
        # (unlike a WorkspaceScope-backed route, there's no user-supplied
        # path to sanitize against traversal here: `name` is checked against
        # the exact `<32 hex chars>.png` shape render_thumbnail always
        # generates, so it can only ever resolve to a plain filename inside
        # that one directory).
        if not _PREVIEW_NAME_RE.fullmatch(name):
            return JSONResponse({"error": "not found"}, status_code=404)
        preview_path = settings.state_dir / "previews" / name
        if not preview_path.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(preview_path, media_type="image/png")

    @router.get("/api/screenshots/{name}")
    async def get_screenshot(name: str) -> Response:
        # Same shape check as previews: only a page_screenshot file name.
        if not _PREVIEW_NAME_RE.fullmatch(name):
            return JSONResponse({"error": "not found"}, status_code=404)
        path = settings.state_dir / SCREENSHOT_FOLDER / name
        if not path.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(path, media_type="image/png")

    @router.get("/api/pptx-shapes")
    async def get_pptx_shapes(path: str, slide: int, thread_id: str = "") -> JSONResponse:
        # Backs the click-a-shape-in-the-preview-to-target-it feature
        # (ChatLog.tsx's PptxShapeOverlay): the frontend already has
        # `path` from the tool call's own `arguments.path` and picks
        # `slide` from `arguments.slide` (edits) or defaults to 1 (a
        # fresh write_pptx), then overlays clickable regions on top of
        # the already-rendered preview image using this endpoint's
        # inch-based bboxes (converted to on-screen percentages -- see
        # PresentationToolkit.list_pptx_shapes's own docstring for why
        # inches, not pixels). Same underlying method the LLM-facing
        # list_pptx_shapes tool calls -- this is a plain, ungated REST
        # read, not a tool call, since it's UI-only (never reaches the
        # model, never touches the audit log a real tool call would).
        from ...tools.presentations import PresentationToolkit

        if thread_id:
            scope = _get_session(thread_id).workspace_scope()
            toolkit = PresentationToolkit(
                scope.root,
                state_dir=settings.state_dir,
                extra_readable=scope.extra_readable,
                extra_writable=scope.extra_writable,
            )
        else:
            toolkit = PresentationToolkit(settings.workspace_root, state_dir=settings.state_dir)
        try:
            result = await asyncio.to_thread(toolkit.list_pptx_shapes, path=path, slide=slide)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(result)

    @router.get("/api/browse-dirs")
    async def browse_dirs(path: str | None = None) -> dict[str, Any]:
        # Direct port of the old hand-rolled runtime's identical endpoint
        # (deleted in the web cutover, see runtime_lg/README.md) -- missing
        # here entirely was a real live-reported bug: app.js (shared by
        # both backends while they coexisted) drives this folder-browser
        # modal for the Settings panel's extra_readable_dirs/
        # extra_writable_dirs picker, but this app didn't define the route
        # yet at the time, so the fetch 404'd and the modal opened empty/
        # broken with no error surfaced. Same deliberately-unrestricted
        # trust model as before: no auth, local-only, and the user could
        # already type any absolute path into that field by hand.
        target = Path(path).expanduser() if path else Path.home()
        try:
            resolved = target.resolve()
        except OSError as exc:
            return {"error": str(exc)}
        if not resolved.is_dir():
            return {"error": f"Not a directory: {resolved}"}
        directories = []
        try:
            for entry in resolved.iterdir():
                try:
                    if entry.is_dir():
                        directories.append({"name": entry.name, "path": str(entry)})
                except OSError:
                    continue  # unreadable entry (permissions, broken link, ...) -- skip it
        except OSError as exc:
            return {"error": str(exc)}
        return {
            "path": str(resolved),
            "parent": str(resolved.parent) if resolved.parent != resolved else None,
            "directories": sorted(directories, key=lambda d: d["name"].lower()),
        }

    return router
