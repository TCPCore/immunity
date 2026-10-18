"""Integration tests against a live FalkorDB instance.

Skipped automatically when no database is reachable, so the unit suite still
runs on a machine without Docker.

These tests use throwaway graphs and never touch demo data.
"""

from __future__ import annotations

import dataclasses

import pytest

from immunegraph.agents import MARS_CASCADE, MARS_REPEAT
from immunegraph.config import Settings
from immunegraph.db import GraphStore
from immunegraph.swarm import Swarm


def _store(graph_suffix: str) -> GraphStore:
    base = Settings.from_env()
    return GraphStore(dataclasses.replace(base, graph=base.graph + graph_suffix))


def _db_available() -> bool:
    try:
        return _store("_probe").ping()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_available(),
    reason="FalkorDB not reachable, start it with: docker compose up -d",
)


def test_every_load_bearing_query_executes():
    from immunegraph.verify_queries import run

    assert run(verbose=False) == 0


def test_mars_scenario_end_to_end():
    store = _store("_test_demo")
    store.reset()
    store.create_indexes()

    swarm = Swarm(store)
    swarm.start(topic="test", session_id="session_test")

    # --- Act I: the cascade -------------------------------------------------
    turns = [swarm.say(persona, text) for persona, text, _ in MARS_CASCADE]

    assert len(turns[0].claim_ids) == 1
    assert turns[0].derivations == []            # the root has no parent
    assert len(turns[1].derivations) == 1        # Analyst derives from Researcher
    assert len(turns[2].derivations) == 1        # Planner derives from Analyst

    # A conditional must not have been read as a contradiction.
    assert turns[1].contradictions == []

    # --- Act II: detection --------------------------------------------------
    infections = swarm.infections()
    assert len(infections) == 1, infections
    outbreak = infections[0]
    assert outbreak["type"] == "unsupported_root"
    assert outbreak["spread_count"] == 2                 # both downstream claims
    assert outbreak["severity"] == 6.0                   # 1 + 2 claims + 3 agents

    # --- Act III: the immune response --------------------------------------
    results = swarm.dispatch_antibodies()
    assert len(results) == 1
    assert results[0].strategy == "source_citation"
    assert results[0].source_name == "NASA"
    assert results[0].severity_before == 6.0

    # The offender is quarantined. (The infection itself moves to 'contained',
    # which is still reported, contained outbreaks are history worth showing.)
    root_status = store.scalar(
        "MATCH (c:Claim {id: $cid}) RETURN c.status",
        {"cid": results[0].root_claim_id},
    )
    assert root_status == "quarantined"
    assert all(
        i["status"] == "contained" for i in swarm.infections()
    ), swarm.infections()

    # The cascade is contained, not only its root. The Analyst's and Planner's
    # claims descend from the refuted claim and have no independent source, so
    # leaving them as 'active' would show them as "believed" in the UI while
    # their entire justification had just been refuted.
    suspect = [row[0] for row in store.rows("MATCH (c:Claim {status: 'suspect'}) RETURN c.id")]
    assert len(suspect) == 2, f"expected the 2 derived claims to be suspect, got {suspect}"
    assert results[0].cascade_contained, "antibody should report what it contained"

    # --- Act IV: trust decays for the whole chain --------------------------
    trust = {row["agent"]: row["effective_trust"] for row in swarm.trust_table()}
    assert trust["Researcher"] == pytest.approx(0.85)   # asserted the infected claim
    assert trust["Analyst"] == pytest.approx(0.85)      # derived from it
    assert trust["Planner"] == pytest.approx(0.85)      # derived from the derivation

    # --- Act V: immunity recall on a paraphrase ----------------------------
    recall = swarm.recall(MARS_REPEAT[1])
    assert recall.hit
    assert recall.method == "similarity"               # no embeddings configured
    assert recall.past_text is not None

    turn = swarm.say(MARS_REPEAT[0], MARS_REPEAT[1], sweep=False)
    infection = swarm.contain_repeat(turn.claim_ids[0], recall)
    assert infection is not None

    # --- Act VI: the immune system demonstrably worked ---------------------
    effectiveness = {row["infection_type"]: row for row in swarm.effectiveness()}
    assert effectiveness["unsupported_root"]["contained"] == 1
    assert effectiveness["repeat_offender"]["contained"] == 1

    # The repeat is quarantined on sight.
    status = store.scalar(
        "MATCH (c:Claim {id: $cid}) RETURN c.status", {"cid": turn.claim_ids[0]}
    )
    assert status == "quarantined"

    store.graph.delete()


def test_tenant_isolation():
    """Track 02 asks for separate memory per tenant."""
    tenant_a = _store("_tenant_a")
    tenant_b = _store("_tenant_b")
    tenant_a.reset()
    tenant_b.reset()

    tenant_a.query("CREATE (:Claim {id: 'only_in_a', text: 'x'})")

    assert tenant_a.scalar("MATCH (c:Claim {id:'only_in_a'}) RETURN count(c)") == 1
    assert tenant_b.scalar("MATCH (c:Claim {id:'only_in_a'}) RETURN count(c)") == 0

    tenant_a.graph.delete()
    tenant_b.graph.delete()
