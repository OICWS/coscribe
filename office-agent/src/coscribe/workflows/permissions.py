"""What a workflow will touch, worked out from its steps: the sites it
opens, the folders it names, the scripts and other consequential tools it
runs. Shown where the person reviews it -- saving is the consent -- and
what a saved run is held to: a site or folder outside the granted lists
stops the run and waits for an answer.

Only what the steps say outright can be listed. A site or folder that
comes from an input, or that a script decides at run time, isn't known
until then, which is why a run can still stop to ask."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path, PureWindowsPath
from typing import Any
from urllib.parse import urlparse

from .refs import render_text
from .spec import ScriptStep, ToolStep, Workflow, walk

URL_TOOLS = frozenset({"browser_navigate", "browser_tabs"})
SCRIPT_TOOLS = frozenset({"run_python_script", "run_node_script", "run_background_script"})
_FOLDER_ARGS = (
    "save_to",
    "path",
    "file_path",
    "destination",
    "dest",
    "folder",
    "directory",
    "output_path",
)
# What is worth listing: reading and writing in the workspace is the
# ordinary work of a workflow, and the browser's reach is its sites.
_NOTABLE_RISKS = ("EXEC", "EXTERNAL")


def site_of(url: str) -> str | None:
    """"https://www.Example.com/a" -> "example.com", as the desktop app
    names a site: a host, without "www.", and a bare domain covers its
    subdomains."""
    text = url.strip().lower()
    if not text:
        return None
    try:
        host = urlparse(text if "://" in text else f"https://{text}").hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.removeprefix("www.").rstrip(".")
    return host or None


def _is_absolute(text: str) -> bool:
    return Path(text).is_absolute() or PureWindowsPath(text).is_absolute()


def _folder_of(text: str) -> str:
    path = PureWindowsPath(text) if PureWindowsPath(text).drive else Path(text)
    return str(path.parent if path.suffix else path)


def _literal(value: Any, defaults: Mapping[str, Any]) -> str | None:
    """A step argument as text when the steps alone decide it: written out,
    or filled from inputs that have defaults."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return render_text(value, dict(defaults))
    except Exception:  # noqa: BLE001 -- an unresolved reference just means "not known yet"
        return None


def granted_by(workflow: Workflow) -> dict[str, list[str]]:
    """The folders and sites the steps themselves name: what saving the
    workflow allows it."""
    defaults = {i.name: i.default for i in workflow.inputs if i.default is not None}
    folders: list[str] = []
    sites: list[str] = []
    for placed in walk(workflow.steps):
        step = placed.step
        if not isinstance(step, ToolStep):
            continue
        if step.tool in URL_TOOLS:
            url = _literal(step.args.get("url"), defaults)
            site = site_of(url) if url else None
            if site and site not in sites:
                sites.append(site)
        for name in _FOLDER_ARGS:
            text = _literal(step.args.get(name), defaults)
            if text and _is_absolute(text):
                folder = _folder_of(text)
                if folder not in folders:
                    folders.append(folder)
    return {"folders": folders, "sites": sites}


def _unknown_sites(workflow: Workflow) -> list[str]:
    """Steps that open a site taken from an input or an earlier step."""
    defaults = {i.name: i.default for i in workflow.inputs if i.default is not None}
    titles: list[str] = []
    for placed in walk(workflow.steps):
        step = placed.step
        if isinstance(step, ToolStep) and step.tool in URL_TOOLS and step.args.get("url"):
            if _literal(step.args.get("url"), defaults) is None:
                titles.append(step.title)
    return titles


def summarize(
    workflow: Workflow, risks: Mapping[str, str], granted: Mapping[str, list[str]] | None = None
) -> dict[str, Any]:
    """What the review card shows: the granted folders and sites (what the
    steps name, plus anything allowed since), the steps that run scripts,
    the other tools that do something outside the workspace, and the
    steps whose site is only known at run time."""
    named = granted_by(workflow)
    folders = list(dict.fromkeys([*named["folders"], *((granted or {}).get("folders") or [])]))
    sites = list(dict.fromkeys([*named["sites"], *((granted or {}).get("sites") or [])]))
    scripts: list[str] = []
    notable: list[dict[str, str]] = []
    for placed in walk(workflow.steps):
        step = placed.step
        if isinstance(step, ScriptStep) or (
            isinstance(step, ToolStep) and step.tool in SCRIPT_TOOLS
        ):
            scripts.append(step.title)
        elif (
            isinstance(step, ToolStep)
            and not step.tool.startswith("browser_")
            and risks.get(step.tool) in _NOTABLE_RISKS
        ):
            notable.append({"tool": step.tool, "title": step.title, "risk": risks[step.tool]})
    return {
        "folders": folders,
        "sites": sites,
        "scripts": scripts,
        "notable": notable,
        "sites_at_run_time": _unknown_sites(workflow),
    }


_URL = re.compile(r"https?://[^\s\"'\\<>)]+")
_MAX_SITES = 20


def sites_in(outputs: list[Any]) -> list[str]:
    """The sites a run's step outputs show it opened, in order: the browser
    tools name the page they end on."""
    found: list[str] = []
    for match in _URL.finditer(json.dumps(outputs, ensure_ascii=False, default=str)):
        site = site_of(match.group(0))
        if site and site not in found:
            found.append(site)
            if len(found) == _MAX_SITES:
                break
    return found
