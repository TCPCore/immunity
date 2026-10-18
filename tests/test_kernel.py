"""Live tests for the kernel of truth and the sanitiser.

The kernel is the swarm's memory of what is *right*, complementing Infection and
quarantined Claims (memory of what was *wrong*). These tests cover the two claims
that make it worth having:

  1. an antibody's sourced correction becomes a durable fact with provenance
  2. a later claim is judged against that fact without any model call

Skipped automatically when no database is reachable.
"""

from __future__ import annotations

import dataclasses
import time

import pytest

from immunegraph.agents import MARS_CASCADE, MARS_REPEAT
from immunegraph.config import Settings
from immunegraph.db import GraphStore
from immunegraph.kernel import Kernel
from immunegraph.models import Claim, Source
from immunegraph.sanitizer import Sanitizer
from immunegraph.schema import CLAIM_SUSPECT, VERDICT_CLEAN, VERDICT_CONFLICTS, VERDICT_UNKNOWN
from immunegraph.swarm import Swarm


def _store(suffix: str) -> GraphStore:
    base = Settings.from_env()
    return GraphStore(dataclasses.replace(base, graph=base.graph + suffix))


def _db_available() -> bool:
    try:
        return _store("_probe").ping()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_available(),
    reason="FalkorDB not reachable, start it with: docker compose up -d",
)


@pytest.fixture
def store():
    graph = _store("_test_kernel")
    graph.reset()
    graph.create_indexes()
    yield graph
    graph.graph.delete()


# ------------------------------------------------------------------ the kernel


def test_register_fact_records_provenance(store):
    kernel = Kernel(store)
    source = Source(id="src_nasa", name="NASA", url="https://nasa.gov", trust_level="primary")
    store.query(
        "MERGE (s:Source {id:'src_nasa'}) SET s.name=$n, s.url=$u, s.trust_level=$t",
        {"n": source.name, "u": source.url, "t": source.trust_level},
    )

    fact_id = kernel.register_fact("Mars has no breathable oxygen", source=source.id)

    facts = kernel.facts()
    assert len(facts) == 1
    assert facts[0].id == fact_id
    assert facts[0].source == "NASA", "the fact should remember which source backs it"


def test_an_empty_kernel_reports_no_conflicts(store):
    """Regression: count(*) under OPTIONAL MATCH reports 1 for an empty edge set.

    The dashboard was cheerfully claiming a conflict before any fact existed.
    """
    assert Kernel(store).stats() == {"facts": 0, "aligned_edges": 0, "conflict_edges": 0}


def test_supersede_retires_a_fact_without_deleting_it(store):
    kernel = Kernel(store)
    old = kernel.register_fact("Mars has no breathable oxygen")
    new = kernel.register_fact("Mars has no breathable oxygen at any time of day")

    kernel.supersede(old, new)

    active = [f.id for f in kernel.facts()]
    assert active == [new]
    # The history is still there, which is the point of superseding.
    assert store.scalar("MATCH (k:KernelFact {id:$id}) RETURN count(k)", {"id": old}) == 1


# ---------------------------------------------------------------- sanitisation


def _fact(store, text: str) -> str:
    kernel = Kernel(store)
    store.query("MERGE (s:Source {id:'src_x'}) SET s.name='Source X', s.trust_level='primary'")
    return kernel.register_fact(text, source="src_x")


def test_sanitiser_flags_a_claim_that_contradicts_the_kernel(store):
    _fact(store, "Mars has no breathable oxygen")
    writer_claim = Claim(id="c_conflict", text="Mars has oxygen at night")
    store.query(
        "CREATE (c:Claim {id:$id, text:$t, status:'active', fingerprint:'x', "
        "verification:'unsourced', confidence:1.0, created_at:'now'})",
        {"id": writer_claim.id, "t": writer_claim.text},
    )

    verdict = Sanitizer(store).judge(writer_claim.id, writer_claim.text)

    assert verdict.verdict == VERDICT_CONFLICTS
    assert verdict.best_conflict is not None
    assert "breathable oxygen" in verdict.best_conflict["fact"]
    status = store.scalar("MATCH (c:Claim {id:$id}) RETURN c.status", {"id": writer_claim.id})
    assert status == CLAIM_SUSPECT


def test_sanitiser_marks_agreement_as_clean(store):
    _fact(store, "Mars has no breathable oxygen")
    store.query(
        "CREATE (c:Claim {id:'c_clean', text:'Mars has no breathable oxygen', "
        "status:'active', fingerprint:'y', verification:'sourced', confidence:1.0, created_at:'now'})"
    )
    verdict = Sanitizer(store).judge("c_clean", "Mars has no breathable oxygen")
    assert verdict.verdict == VERDICT_CLEAN


def test_sanitiser_says_unknown_when_the_kernel_has_nothing(store):
    _fact(store, "Mars has no breathable oxygen")
    store.query(
        "CREATE (c:Claim {id:'c_other', text:'The launch window opens in April', "
        "status:'active', fingerprint:'z', verification:'unsourced', confidence:1.0, created_at:'now'})"
    )
    verdict = Sanitizer(store).judge("c_other", "The launch window opens in April")
    assert verdict.verdict == VERDICT_UNKNOWN


def test_sanitiser_never_weakens_a_quarantined_claim(store):
    """Detection may already have quarantined it, and quarantined outranks suspect."""
    _fact(store, "Mars has no breathable oxygen")
    store.query(
        "CREATE (c:Claim {id:'c_quar', text:'Mars has oxygen at night', status:'quarantined', "
        "fingerprint:'q', verification:'contradicted', confidence:1.0, created_at:'now'})"
    )
    Sanitizer(store).judge("c_quar", "Mars has oxygen at night")
    status = store.scalar("MATCH (c:Claim {id:'c_quar'}) RETURN c.status")
    assert status == "quarantined", "sanitisation must not downgrade a stronger verdict"


# ------------------------------------------------------------- end to end


def test_antibody_teaches_and_the_sanitiser_learns(store):
    """The whole arc: outbreak, correction, kernel fact, instant recognition."""
    swarm = Swarm(store)
    swarm.start("kernel", "s_kernel")
    for persona, text, _ in MARS_CASCADE:
        swarm.say(persona, text)

    assert swarm.kernel_stats()["facts"] == 0, "nothing learned before the antibody"

    results = swarm.dispatch_antibodies()
    assert results and results[0].fact_id, "a sourced correction must establish a fact"

    stats = swarm.kernel_stats()
    assert stats["facts"] == 1
    assert any(f["source"] == "NASA" for f in swarm.kernel_facts())

    # Now the payload: a *paraphrase* is judged against the kernel.
    started = time.perf_counter()
    turn = swarm.say(MARS_REPEAT[0], MARS_REPEAT[1], sweep=False)
    elapsed_ms = (time.perf_counter() - started) * 1000

    verdict = turn.verdicts[0]
    assert verdict["verdict"] == VERDICT_CONFLICTS, verdict
    assert verdict["overlap"] >= 0.60
    assert verdict["fact"] == "Mars has no breathable oxygen"

    # The point of the fast path: recognition is cheap and needs no cascade scan.
    assert verdict["elapsed_ms"] < 250, verdict
    assert elapsed_ms < 2000, f"fast path took {elapsed_ms:.0f}ms"

    conflicts = swarm.kernel_conflicts()
    assert any(c["claim_id"] == turn.claim_ids[0] for c in conflicts)


def test_demo_dry_run_is_unaffected_by_the_kernel(store):
    """The scripted Mars numbers must not drift because of the kernel work."""
    swarm = Swarm(store)
    swarm.start("kernel", "s_numbers")
    for persona, text, _ in MARS_CASCADE:
        swarm.say(persona, text)
    swarm.dispatch_antibodies()
    assert swarm.infections()[0]["severity"] == 6.0
