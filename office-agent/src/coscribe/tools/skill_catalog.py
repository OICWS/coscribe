"""Skills a user can add from Settings > Skills > Discover, and which skills
are switched on.

Discover lists Apache-2.0 skills from github.com/anthropics/skills and
github.com/anthropics/knowledge-work-plugins, each pinned to one commit in
`skill_catalog.json` (regenerate with scripts/build_skill_catalog.py). Nothing
is bundled: Add downloads a skill's files at its commit into the user's skills
directory and checks each against the catalog's SHA-256, so a changed or
truncated download never lands.

On/off is one global setting per skill, stored in `<state_dir>/skills.json`
as the set of switched-off names; every skill not in it is offered to the
model in every conversation.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from .skills import SkillInfo, load_builtin_skills, load_skills

CATALOG_PATH = Path(__file__).resolve().parent.parent / "skill_catalog.json"
# Written into a Discover skill's folder on install, so the folder is known
# to have come from the catalog (it can be removed and added back).
SOURCE_MARKER = ".coscribe-source.json"
_STATE_FILE = "skills.json"
_RAW_URL = "https://raw.githubusercontent.com/{repo}/{commit}/{src}"

SkillSource = Literal["builtin", "anthropic", "custom"]


class SkillCatalogError(ValueError):
    """Something the user should see: unknown name, already added, a failed
    or mismatched download."""


def load_catalog() -> dict[str, Any]:
    catalog: dict[str, Any] = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return catalog


def skill_source(skill: SkillInfo, builtin_names: set[str]) -> SkillSource:
    if skill.name in builtin_names:
        return "builtin"
    if (skill.dir / SOURCE_MARKER).is_file():
        return "anthropic"
    return "custom"


def disabled_skill_names(state_dir: Path) -> set[str]:
    try:
        data = json.loads((state_dir / _STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {str(name) for name in data.get("disabled", [])} if isinstance(data, dict) else set()


def set_skill_enabled(state_dir: Path, name: str, enabled: bool) -> None:
    disabled = disabled_skill_names(state_dir)
    if enabled:
        disabled.discard(name)
    else:
        disabled.add(name)
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / _STATE_FILE).write_text(
        json.dumps({"disabled": sorted(disabled)}, indent=1), encoding="utf-8"
    )


def all_skills(skills_dir: Path) -> list[SkillInfo]:
    """Built-in skills first; a user skill with a built-in's name is ignored,
    the same precedence the coordinator applies."""
    builtin = load_builtin_skills()
    names = {skill.name for skill in builtin}
    return builtin + [skill for skill in load_skills(skills_dir) if skill.name not in names]


def enabled_skill_names(skills_dir: Path, state_dir: Path) -> set[str]:
    disabled = disabled_skill_names(state_dir)
    return {skill.name for skill in all_skills(skills_dir) if skill.name not in disabled}


def remove_skill(skills_dir: Path, state_dir: Path, name: str) -> None:
    """Deletes a user or Discover skill's folder. Built-in skills can only be
    switched off."""
    builtin_names = {skill.name for skill in load_builtin_skills()}
    if name in builtin_names:
        raise SkillCatalogError(f"{name!r} is built into coscribe -- switch it off instead")
    skill = next((s for s in load_skills(skills_dir) if s.name == name), None)
    if skill is None:
        raise SkillCatalogError(f"No skill named {name!r}")
    if not skill.dir.resolve().is_relative_to(Path(skills_dir).resolve()):
        raise SkillCatalogError(f"{name!r} is outside the skills directory")
    shutil.rmtree(skill.dir)
    set_skill_enabled(state_dir, name, True)


def _download(url: str) -> bytes:
    import httpx

    # trust_env picks up HTTPS_PROXY, same as coscribe's other downloads.
    with httpx.Client(timeout=60, follow_redirects=True, trust_env=True) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.content


def install_catalog_skill(
    skills_dir: Path, name: str, *, fetch: Callable[[str], bytes] | None = None
) -> Path:
    fetch = fetch or _download
    catalog = load_catalog()
    entry = next((s for s in catalog["skills"] if s["name"] == name), None)
    if entry is None:
        raise SkillCatalogError(f"{name!r} isn't in Discover")
    root = Path(skills_dir)
    root.mkdir(parents=True, exist_ok=True)
    target = root / name
    if target.exists() or any(s.name == name for s in all_skills(root)):
        raise SkillCatalogError(f"A skill named {name!r} is already added")

    # Downloaded into a sibling temp folder and moved into place whole, so a
    # failure part-way never leaves a half-written skill that would load.
    staging = Path(tempfile.mkdtemp(prefix=f".{name}-", dir=root))
    try:
        # An entry names its own repository, commit and folder; one that
        # doesn't is in the catalog's default repository under skills/<name>.
        repo = entry.get("repo", catalog["repo"])
        commit = entry.get("commit", catalog["commit"])
        folder = entry.get("path", f"skills/{name}")
        for file in entry["files"]:
            # `src` is where a file lives when that isn't inside the skill's
            # folder (a plugin's license file).
            url = _RAW_URL.format(
                repo=repo, commit=commit, src=file.get("src", f"{folder}/{file['path']}")
            )
            try:
                data = fetch(url)
            except Exception as exc:  # noqa: BLE001 -- any network failure is the user's to see
                raise SkillCatalogError(f"Couldn't download {file['path']}: {exc}") from exc
            if hashlib.sha256(data).hexdigest() != file["sha256"]:
                raise SkillCatalogError(f"{file['path']} didn't match the expected contents")
            dest = (staging / file["path"]).resolve()
            if not dest.is_relative_to(staging.resolve()):
                raise SkillCatalogError(f"Unsafe path in catalog: {file['path']}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        (staging / SOURCE_MARKER).write_text(
            json.dumps({"repo": repo, "commit": commit, "name": name}),
            encoding="utf-8",
        )
        staging.rename(target)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return target


def get_plugin(plugin_id: str) -> dict[str, Any]:
    plugin = next((p for p in load_catalog()["plugins"] if p["id"] == plugin_id), None)
    if plugin is None:
        raise SkillCatalogError(f"{plugin_id!r} isn't in Discover")
    plugin_dict: dict[str, Any] = plugin
    return plugin_dict


def install_plugin(
    skills_dir: Path, plugin_id: str, *, fetch: Callable[[str], bytes] | None = None
) -> list[str]:
    """Adds every skill of a plugin that isn't there yet. All or nothing: if
    one download fails, the skills this call already added are removed again,
    so Add can simply be pressed once more."""
    plugin = get_plugin(plugin_id)
    added: list[str] = []
    try:
        for name in plugin["skills"]:
            if (Path(skills_dir) / name).exists():
                continue
            try:
                install_catalog_skill(skills_dir, name, fetch=fetch)
            except SkillCatalogError as exc:
                if "already added" in str(exc):
                    continue
                raise
            added.append(name)
    except SkillCatalogError:
        for name in added:
            shutil.rmtree(Path(skills_dir) / name, ignore_errors=True)
        raise
    return added


_preview_cache: dict[tuple[str, str], bytes] = {}


def plugin_file(
    plugin_id: str, path: str, *, fetch: Callable[[str], bytes] | None = None
) -> bytes:
    """One file of a plugin as pinned in the catalog, for previewing before
    anything is added. Only paths the catalog lists can be asked for, and the
    download is checked against the catalog's SHA-256 like an install is."""
    plugin = get_plugin(plugin_id)
    file = next((f for f in plugin["files"] if f["path"] == path), None)
    if file is None:
        raise SkillCatalogError(f"No such file: {path!r}")
    key = (plugin["commit"], f"{plugin['path']}/{path}")
    cached = _preview_cache.get(key)
    if cached is not None:
        return cached
    url = _RAW_URL.format(repo=plugin["repo"], commit=plugin["commit"], src=key[1])
    try:
        data = (fetch or _download)(url)
    except Exception as exc:  # noqa: BLE001 -- any network failure is the user's to see
        raise SkillCatalogError(f"Couldn't download {path}: {exc}") from exc
    if hashlib.sha256(data).hexdigest() != file["sha256"]:
        raise SkillCatalogError(f"{path} didn't match the expected contents")
    _preview_cache[key] = data
    return data
