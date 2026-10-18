"""Headless UI tests using Streamlit's AppTest harness.

These actually execute app.py inside a Streamlit runtime and fail on any
uncaught exception, which is the closest thing to a browser smoke test
without a browser.

Every test gets its own write-ahead log. That matters: the log persists across
app instances by design, so without isolation a test can inherit a previous
run's data, which is precisely how the durability feature first announced
itself, by restoring a graph that a test expected to be empty.
"""

from __future__ import annotations

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
    reason="FalkorDB not reachable, start it with: docker compose up -d",
)


@pytest.fixture(autouse=True)
def isolated_log(tmp_path, monkeypatch):
    """Give each test its own log path and a clean graph."""
    import streamlit as st

    monkeypatch.setenv("WRITE_LOG_PATH", str(tmp_path / "writes.db"))
    monkeypatch.setenv("SEED_LOG", "")
    st.cache_resource.clear()

    store = GraphStore(Settings.from_env())
    store.reset()
    store.create_indexes()

    yield

    st.cache_resource.clear()


def _app():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(APP_PATH), default_timeout=120)
    at.run()
    return at


def _button_index(at, needle: str) -> int:
    return next(i for i, b in enumerate(at.button) if needle in b.label)


def test_app_renders_without_exception():
    at = _app()
    assert not at.exception, at.exception
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

    metrics = {m.label: m.value for m in at.metric}
    assert int(metrics.get("Active outbreaks", 0)) + int(metrics.get("Contained", 0)) >= 1

    at.button[_button_index(at, "Re-inject")].click().run()
    assert not at.exception, at.exception
    assert any("Immunity recall fired" in (s.value or "") for s in at.success)


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


def test_rebuild_from_log_button_reports_an_identical_graph():
    """The durability claim, exercised through the UI rather than asserted."""
    at = _app()
    at.button[_button_index(at, "Run Mars")].click().run()
    assert not at.exception
    before = {m.label: m.value for m in at.metric}

    at.button[_button_index(at, "Rebuild from log")].click().run()
    assert not at.exception, at.exception

    notes = [s.value or "" for s in at.success]
    assert any("identical" in note for note in notes), notes

    after = {m.label: m.value for m in at.metric}
    assert before == after, f"dashboard changed across a rebuild: {before} vs {after}"


def test_app_survives_an_empty_graph():
    at = _app()
    assert not at.exception, at.exception
    assert any("No conversation yet" in (c.value or "") for c in at.caption)

    metrics = {m.label: m.value for m in at.metric}
    assert metrics.get("Peak severity") == "0.0"


def test_cold_start_replays_the_committed_seed(monkeypatch):
    """The deployed-app story.

    FalkorDB Cloud's free tier has no persistence, so a fresh instance starts
    empty. With SEED_LOG pointing at the committed log, the first page load
    rebuilds the whole Mars run before rendering anything.
    """
    seed = Path(__file__).resolve().parents[1] / "data" / "seed_writes.jsonl"
    assert seed.is_file(), "the committed seed log is missing"
    monkeypatch.setenv("SEED_LOG", str(seed))

    store = GraphStore(Settings.from_env())
    assert store.node_count() == 0, "the fixture should have wiped the graph"

    at = _app()
    assert not at.exception, at.exception

    assert any("replayed" in (i.value or "") for i in at.info), [i.value for i in at.info]
    assert store.node_count() > 0, "the seed did not repopulate the graph"

    metrics = {m.label: m.value for m in at.metric}
    assert metrics.get("Peak severity") == "6.0", metrics
    assert metrics.get("Facts held") == "1", metrics


def test_app_shows_a_clear_error_when_the_database_is_unreachable(monkeypatch):
    """Judges open the app before starting Docker. It must not traceback.

    Kept last in the file because it repoints the connection at a dead port and
    has to clear the cached connection afterwards.
    """
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    st.cache_resource.clear()
    monkeypatch.setenv("FALKORDB_PORT", "9")  # nothing listens on 9

    try:
        at = AppTest.from_file(str(APP_PATH), default_timeout=60)
        at.run()
        assert not at.exception, at.exception
        assert any(
            "Cannot reach FalkorDB" in (e.value or "") for e in at.error
        ), [e.value for e in at.error]
    finally:
        st.cache_resource.clear()
