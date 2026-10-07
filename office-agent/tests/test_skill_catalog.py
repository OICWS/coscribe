"""The shipped Discover catalog and installing from a second repository."""

import hashlib
import json
from pathlib import Path

import pytest

from coscribe.tools import skill_catalog
from coscribe.tools.skill_catalog import SkillCatalogError, install_catalog_skill, load_catalog


def test_every_listed_skill_is_apache_licensed_and_installs_under_its_own_name() -> None:
    catalog = load_catalog()
    names = [entry["name"] for entry in catalog["skills"]]
    assert len(names) == len(set(names)) and len(names) > 70
    for entry in catalog["skills"]:
        assert entry["license"] == "Apache-2.0"
        assert entry["category"] and entry["description"]
        paths = [f["path"] for f in entry["files"]]
        assert "SKILL.md" in paths and "LICENSE.txt" in paths
        assert all(len(f["sha256"]) == 64 for f in entry["files"])
    # The document skills of anthropics/skills are source-available, not listed.
    assert not {"docx", "pdf", "pptx", "xlsx", "doc-coauthoring"} & set(names)


def test_a_skill_from_the_plugins_repository_is_fetched_from_its_own_folder_with_its_license(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_md = b"---\nname: triage-nda\ndescription: triage\n---\nTriage."
    license_text = b"Apache License\nVersion 2.0"
    catalog = {
        "repo": "anthropics/skills",
        "commit": "aaa",
        "skills": [
            {
                "name": "triage-nda",
                "description": "triage",
                "category": "Legal",
                "license": "Apache-2.0",
                "repo": "anthropics/knowledge-work-plugins",
                "commit": "bbb",
                "path": "legal/skills/triage-nda",
                "files": [
                    {"path": "SKILL.md", "size": 1, "sha256": hashlib.sha256(skill_md).hexdigest()},
                    {
                        "path": "LICENSE.txt",
                        "src": "legal/LICENSE",
                        "size": 1,
                        "sha256": hashlib.sha256(license_text).hexdigest(),
                    },
                ],
            }
        ],
    }
    monkeypatch.setattr(skill_catalog, "load_catalog", lambda: catalog)
    raw = "https://raw.githubusercontent.com/anthropics/knowledge-work-plugins/bbb"
    served = {
        f"{raw}/legal/skills/triage-nda/SKILL.md": skill_md,
        f"{raw}/legal/LICENSE": license_text,
    }

    target = install_catalog_skill(tmp_path / "skills", "triage-nda", fetch=served.__getitem__)

    assert (target / "SKILL.md").read_bytes() == skill_md
    assert (target / "LICENSE.txt").read_bytes() == license_text
    marker = json.loads((target / skill_catalog.SOURCE_MARKER).read_text(encoding="utf-8"))
    assert marker == {
        "repo": "anthropics/knowledge-work-plugins",
        "commit": "bbb",
        "name": "triage-nda",
    }
    with pytest.raises(SkillCatalogError, match="already added"):
        install_catalog_skill(tmp_path / "skills", "triage-nda", fetch=served.__getitem__)
