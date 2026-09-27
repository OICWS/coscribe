"""Regenerate src/coscribe/skill_catalog.json from a local clone of
github.com/anthropics/skills checked out at the commit to pin.

    python scripts/build_skill_catalog.py /path/to/anthropics-skills

Only skills listed in SKILLS are included: Apache-2.0 ones that work with
coscribe's own tools as they are. The document skills (docx/pdf/pptx/xlsx)
are source-available under terms that forbid redistribution, so they are
never listed.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import yaml

SKILLS = [
    "algorithmic-art",
    "brand-guidelines",
    "canvas-design",
    "claude-api",
    "frontend-design",
    "internal-comms",
    "mcp-builder",
    "theme-factory",
]

OUT = Path(__file__).resolve().parent.parent / "src" / "coscribe" / "skill_catalog.json"


def main(clone: Path) -> None:
    commit = subprocess.check_output(
        ["git", "-C", str(clone), "rev-parse", "HEAD"], text=True
    ).strip()
    entries = []
    for name in SKILLS:
        root = clone / "skills" / name
        license_text = (root / "LICENSE.txt").read_text(encoding="utf-8")
        if "Apache License" not in license_text:
            raise SystemExit(f"{name}: not Apache-2.0, refusing to list it")
        _, raw, _ = (root / "SKILL.md").read_text(encoding="utf-8").split("---", 2)
        meta = yaml.safe_load(raw)
        # Install downloads from skills/<name>/, so the name must be the folder's.
        if meta["name"] != name:
            raise SystemExit(f"{name}: SKILL.md names it {meta['name']!r}, not its folder name")
        files = []
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            data = path.read_bytes()
            files.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
        entries.append(
            {
                "name": meta["name"],
                "description": meta["description"],
                "license": "Apache-2.0",
                "files": files,
            }
        )
    OUT.write_text(
        json.dumps({"repo": "anthropics/skills", "commit": commit, "skills": entries}, indent=1)
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(entries)} skills at {commit[:12]} to {OUT}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
