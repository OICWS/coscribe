"""Skills routes."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import (
    APIRouter,
    UploadFile,
)
from fastapi.responses import JSONResponse

from ...tools import (
    SkillUploadError,
    load_builtin_skills,
    save_uploaded_skill,
)
from ...tools._workspace import WorkspaceScope
from ...tools.skill_catalog import SOURCE_MARKER as SKILL_SOURCE_MARKER
from ...tools.skill_catalog import (
    SkillCatalogError,
    all_skills,
    disabled_skill_names,
    get_plugin,
    install_catalog_skill,
    install_plugin,
    load_catalog,
    plugin_file,
    remove_skill,
    set_skill_enabled,
    skill_source,
)
from ..connector_catalog import MCP_CATALOG
from ..schemas import (
    SkillEnabledUpdate,
)
from ..state import AppState
from .shared import MAX_UPLOAD_BYTES


def _connector_url_key(url: str) -> str:
    return url.split("#")[0].rstrip("/").lower()


def router(state: AppState) -> APIRouter:
    router = APIRouter()
    settings = state.settings
    _skills_by_name = state.skills_by_name


    @router.get("/api/skills")
    async def get_skills() -> list[dict[str, Any]]:
        builtin_names = {skill.name for skill in load_builtin_skills()}
        disabled = disabled_skill_names(settings.state_dir)
        result = []
        for skill in all_skills(settings.skills_dir):
            updated = datetime.fromtimestamp((skill.dir / "SKILL.md").stat().st_mtime, UTC)
            result.append(
                {
                    "name": skill.name,
                    "description": skill.description,
                    "source": skill_source(skill, builtin_names),
                    "enabled": skill.name not in disabled,
                    "updated": updated.isoformat(),
                }
            )
        return result

    @router.post("/api/skills/{name}/enabled")
    async def update_skill_enabled(name: str, payload: SkillEnabledUpdate) -> JSONResponse:
        if name not in _skills_by_name():
            return JSONResponse({"error": f"No skill named {name!r}."}, status_code=404)
        set_skill_enabled(settings.state_dir, name, payload.enabled)
        return JSONResponse({"name": name, "enabled": payload.enabled})

    @router.delete("/api/skills/{name}")
    async def delete_skill(name: str) -> JSONResponse:
        try:
            await asyncio.to_thread(remove_skill, settings.skills_dir, settings.state_dir, name)
        except SkillCatalogError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"removed": name})

    @router.get("/api/skills/catalog")
    async def get_skill_catalog() -> list[dict[str, Any]]:
        installed = set(_skills_by_name())
        return [
            {
                "name": entry["name"],
                "description": entry["description"],
                "license": entry["license"],
                "category": entry.get("category", "Other"),
                "size": sum(file["size"] for file in entry["files"]),
                "added": entry["name"] in installed,
            }
            for entry in load_catalog()["skills"]
        ]

    @router.post("/api/skills/catalog/{name}")
    async def add_catalog_skill(name: str) -> JSONResponse:
        try:
            await asyncio.to_thread(install_catalog_skill, settings.skills_dir, name)
        except SkillCatalogError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        set_skill_enabled(settings.state_dir, name, True)
        return JSONResponse({"added": name})

    _SKILL_FILE_PREVIEW_MAX_BYTES = 500_000

    def _plugin_summary(plugin: dict[str, Any]) -> dict[str, Any]:
        installed = set(_skills_by_name())
        return {
            "id": plugin["id"],
            "title": plugin["title"],
            "author": plugin["author"],
            "repo": plugin["repo"],
            "version": plugin["version"],
            "description": plugin["description"],
            "license": plugin["license"],
            "updated": plugin["updated"],
            "skills": plugin["skills"],
            "added": sum(1 for n in plugin["skills"] if n in installed),
        }

    @router.get("/api/skills/plugins")
    async def get_skill_plugins() -> list[dict[str, Any]]:
        return [_plugin_summary(p) for p in load_catalog()["plugins"]]

    @router.get("/api/skills/plugins/{plugin_id}")
    async def get_skill_plugin(plugin_id: str) -> JSONResponse:
        try:
            plugin = get_plugin(plugin_id)
        except SkillCatalogError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        hosted = [e for e in MCP_CATALOG if e.get("server_url")]
        by_url = {_connector_url_key(e["server_url"]): e["name"] for e in hosted}
        names = {e["name"] for e in hosted}

        def ours(c: dict[str, Any]) -> str | None:
            found = by_url.get(_connector_url_key(c["url"])) if c["url"] else None
            plain = c["name"].replace(" ", "-")
            return found or (plain if plain in names else None)

        descriptions = {s["name"]: s["description"] for s in load_catalog()["skills"]}
        return JSONResponse(
            {
                **_plugin_summary(plugin),
                "files": [f["path"] for f in plugin["files"]],
                "skill_details": [
                    {"name": n, "description": descriptions.get(n, "")} for n in plugin["skills"]
                ],
                # `connector` is the coscribe connector that talks to the same
                # server, or null when coscribe has none for it.
                "connectors": [
                    {**c, "connector": ours(c)} for c in plugin["connectors"]
                ],
            }
        )

    @router.get("/api/skills/plugins/{plugin_id}/files/{path:path}")
    async def get_skill_plugin_file(plugin_id: str, path: str) -> JSONResponse:
        try:
            plugin = get_plugin(plugin_id)
        except SkillCatalogError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        entry = next((f for f in plugin["files"] if f["path"] == path), None)
        if entry is None:
            return JSONResponse({"error": f"No such file: {path!r}."}, status_code=404)
        if entry["size"] > _SKILL_FILE_PREVIEW_MAX_BYTES:
            return JSONResponse(
                {"error": f"File is {entry['size']:,} bytes -- too large to preview here."},
                status_code=413,
            )
        try:
            data = await asyncio.to_thread(plugin_file, plugin_id, path)
            return JSONResponse({"path": path, "content": data.decode("utf-8")})
        except UnicodeDecodeError:
            return JSONResponse(
                {"error": "This file isn't UTF-8 text -- can't preview it here."}, status_code=415
            )
        except SkillCatalogError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)

    @router.post("/api/skills/plugins/{plugin_id}")
    async def add_skill_plugin(plugin_id: str) -> JSONResponse:
        try:
            added = await asyncio.to_thread(install_plugin, settings.skills_dir, plugin_id)
        except SkillCatalogError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        for name in added:
            set_skill_enabled(settings.state_dir, name, True)
        return JSONResponse({"added": added})

    @router.get("/api/skills/{name}/files")
    async def get_skill_files(name: str) -> JSONResponse:
        # Backs the Skills settings tab's own file-tree browser (a skill
        # is a real directory -- SKILL.md plus whatever reference docs/
        # scripts it needs -- so "click a skill to see its folder" is a
        # real filesystem listing, not a database query). Flat list of
        # relative posix paths, same shape files.py's own list_files tool
        # already returns -- the frontend folds path segments into a tree
        # client-side rather than this endpoint building nested JSON.
        skill = _skills_by_name().get(name)
        if skill is None:
            return JSONResponse({"error": f"No skill named {name!r}."}, status_code=404)
        files = sorted(
            path.relative_to(skill.dir).as_posix()
            for path in skill.dir.rglob("*")
            if path.is_file() and path.name != SKILL_SOURCE_MARKER
        )
        return JSONResponse({"files": files})

    # A skill's own files are typically short prose/scripts meant to be
    # read whole, not paginated -- this cap exists only to stop a genuinely
    # huge file (an accidentally-included data dump) from being sent whole
    # to the browser, matching search_files/list_files' own "bounded, not
    # unlimited" caps elsewhere in this codebase.

    @router.get("/api/skills/{name}/files/{path:path}")
    async def get_skill_file_content(name: str, path: str) -> JSONResponse:
        skill = _skills_by_name().get(name)
        if skill is None:
            return JSONResponse({"error": f"No skill named {name!r}."}, status_code=404)
        scope = WorkspaceScope(skill.dir)
        try:
            file_path = scope.resolve(path)
        except PermissionError:
            return JSONResponse(
                {"error": "Path is outside this skill's own directory."}, status_code=400
            )
        if not file_path.is_file():
            return JSONResponse({"error": f"No such file: {path!r}."}, status_code=404)
        size = file_path.stat().st_size
        if size > _SKILL_FILE_PREVIEW_MAX_BYTES:
            return JSONResponse(
                {"error": f"File is {size:,} bytes -- too large to preview here."}, status_code=413
            )
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return JSONResponse(
                {"error": "This file isn't UTF-8 text -- can't preview it here."}, status_code=415
            )
        return JSONResponse({"path": path, "content": content})

    @router.post("/api/skills/upload")
    async def upload_skill(file: UploadFile) -> JSONResponse:
        # The real half of Settings > Skills > Add > Upload skill (see
        # tools/skills.py's save_uploaded_skill for the accepted shapes).
        # Always writes into settings.skills_dir, i.e.
        # always a "custom" skill -- there's no UI path to add a builtin
        # one, those only ever come from the package itself.
        content = await file.read()
        if len(content) > MAX_UPLOAD_BYTES:
            return JSONResponse({"error": "file too large (max 25MB)"}, status_code=413)
        try:
            skill = save_uploaded_skill(settings.skills_dir, file.filename or "", content)
        except SkillUploadError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(
            {"name": skill.name, "description": skill.description, "source": "custom"}
        )

    return router
