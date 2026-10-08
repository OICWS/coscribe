"""Regenerate src/coscribe/skill_catalog.json from local clones of the two
repositories Discover draws from, each checked out at the commit to pin.

    python scripts/build_skill_catalog.py /path/to/anthropics-skills \
        /path/to/anthropics-knowledge-work-plugins

Only skills named below are listed: Apache-2.0 ones that work with coscribe's
own tools as they are. A skill's license file must say Apache License, or the
script refuses to list it. The document skills (docx/pdf/pptx/xlsx) of
anthropics/skills are source-available under terms that forbid
redistribution, so they are never listed, and neither is anything whose license
is unclear (doc-coauthoring has none).

From anthropics/knowledge-work-plugins only the role plugins' skills that need
no connected service to be useful are taken (each asks for a contract, a
spreadsheet, a pasted email, and so on); small-business and partner-built are
tied to ledgers and CRMs, and engineering and bio-research are for developers
and scientists. Names that two plugins share are taken from one, and names
too general to stand alone in a skill list (analyze, brief) are left out.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import yaml

# (category, skill folder names) for anthropics/skills, which keeps each
# skill's own LICENSE.txt.
ANTHROPIC_SKILLS = [
    (
        "Design & creative",
        [
            "algorithmic-art",
            "brand-guidelines",
            "canvas-design",
            "slack-gif-creator",
            "theme-factory",
        ],
    ),
    ("Writing", ["internal-comms"]),
    ("Developer", ["claude-api", "frontend-design", "mcp-builder"]),
]

# (category, plugin folder, skill names) for anthropics/knowledge-work-plugins.
# Its skills sit at <plugin>/skills/<name>/ and carry the plugin's LICENSE (or
# the repository's, for a plugin without one).
KNOWLEDGE_WORK = [
    (
        "Legal",
        "legal",
        [
            "review-contract",
            "triage-nda",
            "compliance-check",
            "legal-risk-assessment",
            "legal-response",
            "meeting-briefing",
        ],
    ),
    (
        "Finance",
        "finance",
        [
            "audit-support",
            "close-management",
            "financial-statements",
            "journal-entry",
            "reconciliation",
            "sox-testing",
            "variance-analysis",
        ],
    ),
    (
        "Marketing",
        "marketing",
        [
            "brand-review",
            "campaign-plan",
            "competitive-brief",
            "content-creation",
            "draft-content",
            "email-sequence",
            "performance-report",
            "seo-audit",
        ],
    ),
    (
        "Operations",
        "operations",
        [
            "capacity-plan",
            "change-request",
            "compliance-tracking",
            "process-doc",
            "process-optimization",
            "risk-assessment",
            "runbook",
            "status-report",
            "vendor-review",
        ],
    ),
    (
        "People & HR",
        "human-resources",
        [
            "comp-analysis",
            "draft-offer",
            "interview-prep",
            "onboarding",
            "org-planning",
            "people-report",
            "performance-review",
            "policy-lookup",
            "recruiting-pipeline",
        ],
    ),
    (
        "Product",
        "product-management",
        [
            "metrics-review",
            "product-brainstorming",
            "roadmap-update",
            "sprint-planning",
            "stakeholder-update",
            "synthesize-research",
            "write-spec",
        ],
    ),
    (
        "Data & analytics",
        "data",
        [
            "build-dashboard",
            "create-viz",
            "data-visualization",
            "explore-data",
            "sql-queries",
            "statistical-analysis",
            "validate-data",
        ],
    ),
    (
        "Design",
        "design",
        [
            "accessibility-review",
            "design-critique",
            "design-handoff",
            "design-system",
            "research-synthesis",
            "user-research",
            "ux-copy",
        ],
    ),
    (
        "Customer support",
        "customer-support",
        [
            "customer-escalation",
            "customer-research",
            "draft-response",
            "kb-article",
            "ticket-triage",
        ],
    ),
    (
        "Sales",
        "sales",
        [
            "account-research",
            "call-summary",
            "competitive-intelligence",
            "create-an-asset",
            "draft-outreach",
            "handle-objection",
        ],
    ),
]

OUT = Path(__file__).resolve().parent.parent / "src" / "coscribe" / "skill_catalog.json"


def _commit(clone: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(clone), "rev-parse", "HEAD"], text=True
    ).strip()


def _entry(
    *,
    clone: Path,
    repo: str,
    commit: str,
    category: str,
    name: str,
    path: str,
    license_src: str,
    license_in_skill: bool,
) -> dict[str, object]:
    root = clone / path
    license_text = (clone / license_src).read_text(encoding="utf-8")
    if "Apache License" not in license_text:
        raise SystemExit(f"{name}: not Apache-2.0, refusing to list it")
    _, raw, _ = (root / "SKILL.md").read_text(encoding="utf-8").split("---", 2)
    meta = yaml.safe_load(raw)
    # Install writes the folder `name`, so SKILL.md must name itself the same.
    if meta["name"] != name:
        raise SystemExit(f"{name}: SKILL.md names it {meta['name']!r}, not its folder name")
    files = []
    for file in sorted(p for p in root.rglob("*") if p.is_file()):
        data = file.read_bytes()
        files.append(
            {
                "path": file.relative_to(root).as_posix(),
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    if not license_in_skill:
        # A plugin's skill folder holds no license file: its plugin's goes with it.
        data = license_text.encode("utf-8")
        files.append(
            {
                "path": "LICENSE.txt",
                "src": license_src,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    description = " ".join(str(meta["description"]).split())
    return {
        "name": name,
        "description": description,
        "category": category,
        "license": "Apache-2.0",
        "repo": repo,
        "commit": commit,
        "path": path,
        "files": files,
    }


PACK_DESCRIPTIONS = {
    "Design & creative": "Creative and design skills: generative art, posters and canvas designs, "
    "themes and brand styling, and animated GIFs for Slack.",
    "Writing": "Writing skills: internal communications in the formats companies use.",
    "Developer": "Skills for developers: the Claude API, front-end design, MCP servers.",
}


def _file_entries(root: Path, only: list[str] | None = None) -> list[dict[str, object]]:
    out = []
    for file in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = file.relative_to(root).as_posix()
        parts = rel.split("/")
        if only is not None and parts[0] == "skills" and parts[1] not in only:
            continue
        data = file.read_bytes()
        out.append({"path": rel, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    return out


def _commit_date(clone: Path, commit: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(clone), "show", "-s", "--format=%cs", commit], text=True
    ).strip()


def _plugin(
    clone: Path, plugin: str, category: str, names: list[str], commit: str
) -> dict[str, object]:
    root = clone / plugin
    meta = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    mcp = (
        json.loads((root / ".mcp.json").read_text(encoding="utf-8"))
        if (root / ".mcp.json").is_file()
        else {}
    )
    connectors = [
        {"name": name, "url": str(config.get("url") or "")}
        for name, config in mcp.get("mcpServers", {}).items()
    ]
    return {
        "id": plugin,
        "title": category,
        "author": meta.get("author", {}).get("name", "Anthropic"),
        "version": meta.get("version", ""),
        "description": meta["description"],
        "license": "Apache-2.0",
        "repo": "anthropics/knowledge-work-plugins",
        "commit": commit,
        "path": plugin,
        "updated": _commit_date(clone, commit),
        "skills": names,
        "connectors": connectors,
        # Only the skills listed, plus the plugin's own README, license and manifests.
        "files": _file_entries(root, names),
    }


def _pack(clone: Path, category: str, names: list[str], commit: str) -> dict[str, object]:
    root = clone / "skills"
    files = []
    for name in names:
        for entry in _file_entries(root / name):
            files.append({**entry, "path": f"{name}/{entry['path']}"})
    return {
        "id": category.lower().replace(" & ", "-").replace(" ", "-"),
        "title": category,
        "author": "Anthropic",
        "version": "",
        "description": PACK_DESCRIPTIONS[category],
        "license": "Apache-2.0",
        "repo": "anthropics/skills",
        "commit": commit,
        "path": "skills",
        "updated": _commit_date(clone, commit),
        "skills": names,
        "connectors": [],
        "files": files,
    }


def main(skills_clone: Path, plugins_clone: Path) -> None:
    entries = []
    plugins = []
    seen: set[str] = set()
    # Work categories first: that is the order Discover lists them in.
    plugins_commit = _commit(plugins_clone)
    for category, plugin, names in KNOWLEDGE_WORK:
        plugin_license = (
            f"{plugin}/LICENSE" if (plugins_clone / plugin / "LICENSE").is_file() else "LICENSE"
        )
        plugins.append(_plugin(plugins_clone, plugin, category, names, plugins_commit))
        for name in names:
            if name in seen:
                raise SystemExit(f"{name}: listed twice")
            seen.add(name)
            entries.append(
                _entry(
                    clone=plugins_clone,
                    repo="anthropics/knowledge-work-plugins",
                    commit=plugins_commit,
                    category=category,
                    name=name,
                    path=f"{plugin}/skills/{name}",
                    license_src=plugin_license,
                    license_in_skill=False,
                )
            )
    skills_commit = _commit(skills_clone)
    for category, names in ANTHROPIC_SKILLS:
        plugins.append(_pack(skills_clone, category, names, skills_commit))
        for name in names:
            if name in seen:
                raise SystemExit(f"{name}: listed twice")
            seen.add(name)
            entries.append(
                _entry(
                    clone=skills_clone,
                    repo="anthropics/skills",
                    commit=skills_commit,
                    category=category,
                    name=name,
                    path=f"skills/{name}",
                    license_src=f"skills/{name}/LICENSE.txt",
                    license_in_skill=True,
                )
            )
    OUT.write_text(
        json.dumps(
            {
                "repo": "anthropics/skills",
                "commit": skills_commit,
                "plugins": plugins,
                "skills": entries,
            },
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(entries)} skills in {len(plugins)} plugins to {OUT}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
