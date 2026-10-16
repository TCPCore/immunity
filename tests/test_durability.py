"""Durability: the write-ahead log and rebuilding the graph from it.

FalkorDB Cloud's free tier has no persistence and no backups, so the graph is
treated as a projection that the log can rebuild. The claim worth testing is not
"the app still works after a rebuild" — it is "the rebuilt graph is provably the
same", ids and statuses included.

The structural test at the bottom is the one that matters most: a write issued
through a read helper is not logged, still appears to work, and is silently lost
on rebuild. That is exactly how this module's own subject matter broke the first
time.
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib

import pytest

from immunegraph.agents import MARS_CASCADE, MARS_REPEAT
from immunegraph.config import Settings
from immunegraph.db import GraphStore
from immunegraph.event_log import WriteLog
from immunegraph.projection import (
    export_jsonl,
    graph_fingerprint,
    import_jsonl,
    rebuild,
)
from immunegraph.swarm import Swarm

SOURCE_DIR = pathlib.Path(__file__).resolve().parents[1] / "src" / "immunegraph"


def _store(suffix: str) -> GraphStore:
    base = Settings.from_env()
    return GraphStore(dataclasses.replace(base, graph=base.graph + suffix))


def _db_available() -> bool:
    try:
        return _store("_probe").ping()
    except Exception:
        return False


live = pytest.mark.skipif(
    not _db_available(),
    reason="FalkorDB not reachable — start it with: docker compose up -d",
)


# ------------------------------------------------------------------- the log


def test_log_records_and_reads_back_in_order():
    log = WriteLog(":memory:")
    log.record("A", "CREATE (n:One)", {"x": 1})
    log.record("B", "CREATE (n:Two)", {"y": 2})

    entries = log.entries()
    assert [e.label for e in entries] == ["A", "B"]
    assert [e.seq for e in entries] == [1, 2]
    assert entries[0].params == {"x": 1}
    assert len(log) == 2


def test_log_can_be_read_since_a_sequence():
    log = WriteLog(":memory:")
    for index in range(5):
        log.record("X", f"CREATE (n:N{index})")
    assert len(log.entries(since=3)) == 2
    assert len(log.entries(limit=2)) == 2


def test_log_clear_empties_it():
    log = WriteLog(":memory:")
    log.record("X", "CREATE (n:N)")
    log.clear()
    assert len(log) == 0


def test_log_label_counts():
    log = WriteLog(":memory:")
    log.record("ClaimAsserted", "CREATE (n:N)")
    log.record("ClaimAsserted", "CREATE (n:N)")
    log.record("AgentRegistered", "CREATE (n:N)")
    assert log.label_counts() == {"ClaimAsserted": 2, "AgentRegistered": 1}


def test_jsonl_round_trip(tmp_path):
    log = WriteLog(":memory:")
    log.record("ClaimAsserted", "CREATE (n:Claim {id:$id})", {"id": "c1"})
    log.record("MessageRecorded", "CREATE (n:Message {id:$id})", {"id": "m1"})

    path = tmp_path / "seed.jsonl"
    assert export_jsonl(log, path) == 2

    restored = WriteLog(":memory:")
    assert import_jsonl(restored, path) == 2
    assert [e.label for e in restored.entries()] == ["ClaimAsserted", "MessageRecorded"]
    assert restored.entries()[0].params == {"id": "c1"}


def test_import_missing_file_is_not_an_error(tmp_path):
    assert import_jsonl(WriteLog(":memory:"), tmp_path / "absent.jsonl") == 0


# --------------------------------------------------------------- live rebuild


def _run_whole_scenario(store) -> Swarm:
    """The full Mars arc, so a rebuild has every kind of write to reproduce."""
    log = WriteLog(":memory:")
    swarm = Swarm(store, log=log)
    swarm.start("durability", "s_dur")
    for persona, text, _ in MARS_CASCADE:
        swarm.say(persona, text)
    swarm.dispatch_antibodies()
    turn = swarm.say(MARS_REPEAT[0], MARS_REPEAT[1], sweep=False)
    swarm.contain_repeat(turn.claim_ids[0], swarm.recall(MARS_REPEAT[1]))
    return swarm


@live
def test_rebuild_reproduces_the_graph_exactly():
    store = _store("_test_durability")
    store.reset()
    store.create_indexes()
    try:
        swarm = _run_whole_scenario(store)
        assert swarm.log_count() > 40, "the scenario should log a lot of writes"

        before = graph_fingerprint(store)
        result = swarm.rebuild_from_log()
        after = graph_fingerprint(store)

        assert result["failed"] == 0, result["failures"]
        assert result["identical"] is True, {
            "nodes_before": len(before["nodes"]),
            "nodes_after": len(after["nodes"]),
            "statuses": (before["claim_statuses"], after["claim_statuses"]),
            "relationships": (before["relationships"], after["relationships"]),
        }
        # Ids must survive: a rebuild with regenerated uuid4 ids would break
        # every reference while still looking structurally plausible.
        assert before["nodes"] == after["nodes"]
        assert before["claim_statuses"] == after["claim_statuses"]
    finally:
        store.graph.delete()


@live
def test_rebuild_is_repeatable():
    store = _store("_test_durability_twice")
    store.reset()
    store.create_indexes()
    try:
        swarm = _run_whole_scenario(store)
        first = graph_fingerprint(store)
        swarm.rebuild_from_log()
        second = graph_fingerprint(store)
        swarm.rebuild_from_log()
        third = graph_fingerprint(store)
        assert first == second == third
    finally:
        store.graph.delete()


@live
def test_replaying_does_not_grow_the_log():
    """Replay uses query(), not write(); re-logging would double the log each run."""
    store = _store("_test_durability_log")
    store.reset()
    store.create_indexes()
    try:
        swarm = _run_whole_scenario(store)
        before = swarm.log_count()
        swarm.rebuild_from_log()
        assert swarm.log_count() == before
    finally:
        store.graph.delete()


@live
def test_exported_log_rebuilds_a_wiped_graph(tmp_path):
    """The deployed-app story: a committed seed log restores everything."""
    store = _store("_test_durability_seed")
    store.reset()
    store.create_indexes()
    try:
        swarm = _run_whole_scenario(store)
        expected = graph_fingerprint(store)
        path = tmp_path / "seed.jsonl"
        assert export_jsonl(swarm.log, path) > 40

        # Simulate a cold start: brand new graph, brand new log, seed imported.
        store.reset()
        store.create_indexes()
        fresh = WriteLog(":memory:")
        import_jsonl(fresh, path)
        result = rebuild(store, fresh)

        assert result["failed"] == 0
        assert graph_fingerprint(store) == expected
    finally:
        store.graph.delete()


@live
def test_a_write_is_logged_only_once():
    store = _store("_test_durability_once")
    store.reset()
    store.create_indexes()
    try:
        log = WriteLog(":memory:")
        swarm = Swarm(store, log=log)
        swarm.start("once", "s_once")
        swarm.say(MARS_CASCADE[0][0], MARS_CASCADE[0][1])
        # One turn writes several statements, none of them twice.
        assert len(log) == len(log.entries())
    finally:
        store.graph.delete()


@live
def test_reset_all_clears_the_log_as_well_as_the_graph():
    """Otherwise a rebuild replays a wiped run and claim ids collide.

    Claim ids come from a per-session counter that restarts at zero, so the
    second run's cl_..._0001 would be created alongside the first run's.
    """
    store = _store("_test_durability_reset")
    store.reset()
    store.create_indexes()
    try:
        log = WriteLog(":memory:")
        swarm = Swarm(store, log=log)
        swarm.start("first", "s_first")
        for persona, text, _ in MARS_CASCADE:
            swarm.say(persona, text)
        assert swarm.log_count() > 0

        swarm.reset_all()
        assert swarm.log_count() == 0
        assert store.node_count() == 0

        # A rebuild now has nothing to apply and must not resurrect the run.
        result = swarm.rebuild_from_log()
        assert result["applied"] == 0
        assert store.node_count() == 0

        # And running again produces the same ids without collision.
        swarm.start("second", "s_first")
        for persona, text, _ in MARS_CASCADE:
            swarm.say(persona, text)
        duplicates = store.rows(
            """
            MATCH (c:Claim)
            WITH c.id AS id, count(*) AS n
            WHERE n > 1
            RETURN id
            """
        )
        assert duplicates == [], f"duplicate claim ids after reset: {duplicates}"
    finally:
        store.graph.delete()


# ------------------------------------------------------- structural guardrail


def test_no_write_hides_inside_a_read_call():
    """A write issued through rows()/scalar()/one() is never logged.

    It still works — and a rebuild silently loses it. This is not hypothetical:
    the cascade containment update was written that way, and the graph
    fingerprint is what caught it. A rebuild looked fine by every other measure
    while two claims quietly reverted from 'suspect' to 'active'.
    """
    write_words = ("CREATE ", "SET ", "MERGE ", "DELETE ", "REMOVE ")
    read_methods = {"rows", "scalar", "one", "dicts"}
    offenders: list[str] = []

    for path in sorted(SOURCE_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr not in read_methods:
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    hits = [w for w in write_words if w in arg.value.upper()]
                    if hits:
                        offenders.append(f"{path.name}:{node.lineno} via {func.attr}() {hits}")

    assert not offenders, (
        "these writes bypass the write-ahead log, so a rebuild will lose them: "
        + "; ".join(offenders)
    )


def test_every_mutating_writer_method_is_labelled():
    """An unlabelled write still replays, but shows up as 'Write' in the log."""
    path = SOURCE_DIR / "memory.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    unlabelled = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != "MemoryWriter":
            continue
        for item in node.body:
            if not isinstance(item, ast.FunctionDef):
                continue
            body = ast.dump(ast.Module(body=item.body, type_ignores=[]))
            if "self._write(" not in ast.unparse(item):
                continue  # a pure read, nothing to label
            labels = [
                ast.unparse(d) for d in item.decorator_list
            ]
            if not any("logs(" in text for text in labels):
                unlabelled.append(item.name)

    assert not unlabelled, f"these write but carry no log label: {unlabelled}"
