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


def main(skills_clone: Path, plugins_clone: Path) -> None:
    entries = []
    seen: set[str] = set()
    # Work categories first: that is the order Discover lists them in.
    plugins_commit = _commit(plugins_clone)
    for category, plugin, names in KNOWLEDGE_WORK:
        plugin_license = (
            f"{plugin}/LICENSE" if (plugins_clone / plugin / "LICENSE").is_file() else "LICENSE"
        )
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
            {"repo": "anthropics/skills", "commit": skills_commit, "skills": entries}, indent=1
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(entries)} skills to {OUT}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
