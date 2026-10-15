"""Documentation integrity tests.

A broken relative link or an unbalanced code fence in a README that judges
read before anything else is an avoidable embarrassment, and neither is caught
by any other test. These checks cost nothing and run without a database.

Deliberately stdlib-only: no markdown library, because the failure modes we
care about (missing target file, unbalanced fences) are cheap to detect.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_FILES = [REPO_ROOT / "README.md", *sorted((REPO_ROOT / "docs").glob("*.md"))]

# [text](target) and ![alt](target). Skips absolute URLs and bare anchors.
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
FENCE_RE = re.compile(r"^\s*```", re.MULTILINE)


def _relative_targets(text: str) -> list[str]:
    targets = []
    for raw in LINK_RE.findall(text):
        target = raw.split("#", 1)[0].strip()
        if not target or target.startswith(("http://", "https://", "mailto:")):
            continue
        targets.append(target)
    return targets


def test_markdown_files_exist():
    assert MARKDOWN_FILES, "no markdown files discovered"
    for path in MARKDOWN_FILES:
        assert path.is_file(), path


@pytest.mark.parametrize("md_path", MARKDOWN_FILES, ids=lambda p: p.name)
def test_relative_links_resolve(md_path: Path):
    missing = []
    for target in _relative_targets(md_path.read_text(encoding="utf-8")):
        resolved = (md_path.parent / target).resolve()
        if not resolved.exists():
            missing.append(target)
    assert not missing, f"{md_path.name} has broken links: {missing}"


@pytest.mark.parametrize("md_path", MARKDOWN_FILES, ids=lambda p: p.name)
def test_code_fences_are_balanced(md_path: Path):
    fences = len(FENCE_RE.findall(md_path.read_text(encoding="utf-8")))
    assert fences % 2 == 0, f"{md_path.name} has {fences} code fences (odd = unbalanced)"


def test_readme_hero_image_exists_and_is_referenced():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/images/immunegraph-ui.png" in readme, "README no longer shows a screenshot"
    assert (REPO_ROOT / "docs/images/immunegraph-ui.png").is_file()


def test_required_submission_documents_present():
    """Rule 10 names specific artefacts; make sure they cannot go missing."""
    required = [
        "README.md",
        "LICENSE",
        "docs/GRAPH_MODEL.md",
        "docs/COMPLIANCE.md",
        "docs/EXPLAINER.md",
        "docs/SUBMISSION.md",
        "docs/DEMO_SCRIPT.md",
        "docker-compose.yml",
        ".env.example",
        "app.py",
    ]
    missing = [name for name in required if not (REPO_ROOT / name).exists()]
    assert not missing, f"missing required files: {missing}"


def test_readme_names_both_tracks_and_discloses_ai_use():
    """Rules 5, 10 and 11 must be visible in the README itself."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "Agents That Act on Connected Data" in readme
    assert "Agent Memory and Coordination" in readme
    assert "AI coding assistant disclosure" in readme


def test_readme_contains_the_cypher_the_product_depends_on():
    """Rule 10 asks for the queries, so at least the headline ones must be inline."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "DERIVED_FROM*1..5" in readme, "Q4 (severity) is not shown in the README"
    assert "SUPPORTED_BY|DERIVED_FROM*2..5" in readme, "Q1 (cycles) is not shown in the README"
    assert "fingerprint: $fingerprint" in readme, "Q6 (immunity recall) is not shown"
