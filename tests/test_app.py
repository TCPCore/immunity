"""Headless UI tests using Streamlit's AppTest harness.

These actually execute app.py inside a Streamlit runtime and fail on any
uncaught exception, which is the closest thing to a browser smoke test
without a browser.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from immunegraph.config import Settings
from immunegraph.db import GraphStore

# AppTest resolves relative paths against the calling file, so give it an
# absolute path to the repository root's app.py.
APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def _db_available() -> bool:
    try:
        return GraphStore(Settings.from_env()).ping()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_available(),
    reason="FalkorDB not reachable — start it with: docker compose up -d",
)


def _app() -> "object":
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP_PATH), default_timeout=120)
    at.run()
    return at


def _button_index(at, needle: str) -> int:
    return next(i for i, b in enumerate(at.button) if needle in b.label)


def test_app_renders_without_exception():
    at = _app()
    assert not at.exception, at.exception
    # The three headline panels must be present.
    assert len(at.title) >= 1
    assert any("ImmuneGraph" in t.value for t in at.title)


def test_app_runs_the_mission_and_recalls_immunity():
    at = _app()
    assert not at.exception

    labels = [b.label for b in at.button]
    assert any("Run Mars" in label for label in labels), labels
    assert any("Re-inject" in label for label in labels), labels

    at.button[_button_index(at, "Run Mars")].click().run()
    assert not at.exception, at.exception

    # The dashboard should now report at least one outbreak.
    metrics = {m.label: m.value for m in at.metric}
    assert int(metrics.get("Active outbreaks", 0)) + int(metrics.get("Contained", 0)) >= 1

    at.button[_button_index(at, "Re-inject")].click().run()
    assert not at.exception, at.exception

    # Immunity recall should have fired and said so.
    assert any(
        "Immunity recall fired" in (s.value or "") for s in at.success
    ), [s.value for s in at.success]


def test_reinject_twice_does_not_crash_on_an_exact_match():
    """Regression test for a bug that would have bitten on stage.

    The first re-injection matches the paraphrase by similarity and carries a
    score. The second matches the now-quarantined identical claim by
    fingerprint, which carries no score, so formatting one raises TypeError.
    """
    at = _app()
    at.button[_button_index(at, "Run Mars")].click().run()
    assert not at.exception

    at.button[_button_index(at, "Re-inject")].click().run()
    assert not at.exception, at.exception
    first = [s.value for s in at.success]

    at.button[_button_index(at, "Re-inject")].click().run()
    assert not at.exception, at.exception
    second = [s.value for s in at.success]

    assert any("score" in (value or "") for value in first), first
    assert any("exact match" in (value or "") for value in second), second


def test_rerunning_the_mission_replays_deterministically():
    """A stale claim must not become the derivation parent of the next run."""
    at = _app()
    at.button[_button_index(at, "Run Mars")].click().run()
    assert not at.exception
    first = {m.label: m.value for m in at.metric}

    at.button[_button_index(at, "Run Mars")].click().run()
    assert not at.exception
    second = {m.label: m.value for m in at.metric}

    assert first == second, f"second run differed: {first} vs {second}"


def test_app_survives_an_empty_graph():
    store = GraphStore(Settings.from_env())
    store.reset()
    store.create_indexes()

    at = _app()
    assert not at.exception, at.exception
    assert any("No conversation yet" in (c.value or "") for c in at.caption)
