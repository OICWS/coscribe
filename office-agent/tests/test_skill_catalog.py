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


def _plugin_catalog() -> tuple[dict, dict[str, bytes]]:
    md = {n: f"---\nname: {n}\ndescription: d\n---\n{n}".encode() for n in ("one", "two")}
    lic = b"Apache License\nVersion 2.0"
    raw = "https://raw.githubusercontent.com/o/r/ccc"

    def entry(name: str) -> dict:
        return {
            "name": name,
            "description": "d",
            "category": "Legal",
            "license": "Apache-2.0",
            "repo": "o/r",
            "commit": "ccc",
            "path": f"pl/skills/{name}",
            "files": [
                {"path": "SKILL.md", "size": 1, "sha256": hashlib.sha256(md[name]).hexdigest()},
                {
                    "path": "LICENSE.txt",
                    "src": "pl/LICENSE",
                    "size": 1,
                    "sha256": hashlib.sha256(lic).hexdigest(),
                },
            ],
        }

    catalog = {
        "repo": "o/r",
        "commit": "ccc",
        "plugins": [
            {
                "id": "pl",
                "repo": "o/r",
                "commit": "ccc",
                "path": "pl",
                "skills": ["one", "two"],
                "files": [
                    {
                        "path": f"skills/{n}/SKILL.md",
                        "size": 1,
                        "sha256": hashlib.sha256(md[n]).hexdigest(),
                    }
                    for n in md
                ],
            }
        ],
        "skills": [entry("one"), entry("two")],
    }
    served = {f"{raw}/pl/skills/{n}/SKILL.md": md[n] for n in md} | {f"{raw}/pl/LICENSE": lic}
    return catalog, served


def test_adding_a_plugin_adds_every_skill_and_a_failure_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog, served = _plugin_catalog()
    monkeypatch.setattr(skill_catalog, "load_catalog", lambda: catalog)
    skills = tmp_path / "skills"
    broken = dict(served)
    del broken["https://raw.githubusercontent.com/o/r/ccc/pl/skills/two/SKILL.md"]

    with pytest.raises(SkillCatalogError):
        skill_catalog.install_plugin(skills, "pl", fetch=broken.__getitem__)
    assert not (skills / "one").exists() and not (skills / "two").exists()

    assert skill_catalog.install_plugin(skills, "pl", fetch=served.__getitem__) == ["one", "two"]
    assert skill_catalog.install_plugin(skills, "pl", fetch=served.__getitem__) == []


def test_a_plugin_file_is_checked_against_the_catalog_and_only_listed_paths_are_served(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog, served = _plugin_catalog()
    monkeypatch.setattr(skill_catalog, "load_catalog", lambda: catalog)
    skill_catalog._preview_cache.clear()

    data = skill_catalog.plugin_file("pl", "skills/one/SKILL.md", fetch=served.__getitem__)
    assert data.startswith(b"---")
    with pytest.raises(SkillCatalogError, match="No such file"):
        skill_catalog.plugin_file("pl", "../../etc/passwd", fetch=served.__getitem__)
    skill_catalog._preview_cache.clear()
    with pytest.raises(SkillCatalogError, match="didn't match"):
        skill_catalog.plugin_file("pl", "skills/one/SKILL.md", fetch=lambda _u: b"tampered")


def test_every_plugin_lists_skills_that_exist_and_files_with_hashes() -> None:
    catalog = load_catalog()
    skills = {s["name"] for s in catalog["skills"]}
    ids = [p["id"] for p in catalog["plugins"]]
    assert len(ids) == len(set(ids)) == 13
    assert set().union(*(p["skills"] for p in catalog["plugins"])) == skills
    for plugin in catalog["plugins"]:
        assert plugin["title"] and plugin["description"] and plugin["updated"]
        assert all(len(f["sha256"]) == 64 for f in plugin["files"])
        for name in plugin["skills"]:
            assert any(f["path"].endswith(f"{name}/SKILL.md") for f in plugin["files"])
