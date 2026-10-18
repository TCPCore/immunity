"""Track 03: a company brain over one company's own documents.

The unit tests cover the quantity-conflict rule with no database at all, because
that rule is where a company brain actually fails. Everything a company states
as fact is a number, price, SLA, retention window, seat count, and negation
cannot see "399" disagreeing with "499".

The scenario tests assert the whole story: documents become truth, a wrong price
spreads between departments, it is contained, and then the price changes and the
*previously correct* answer is caught too.
"""

from __future__ import annotations

import dataclasses

import pytest

from immunegraph.company import (
    COMPANY,
    DOCUMENTS,
    OLD_PRICE,
    WRONG_PRICE,
    CompanyBrain,
)
from immunegraph.config import Settings
from immunegraph.db import GraphStore
from immunegraph.models import conflicts_on_numbers, numbers_in
from immunegraph.swarm import Swarm

FACT = "The Enterprise plan costs 499 USD per seat per year"


# ------------------------------------------------------------- numeric conflict


def test_numbers_are_extracted_and_normalised():
    assert numbers_in("1,499 USD") == {"1499"}
    assert numbers_in("no digits here") == set()


def test_a_different_price_is_a_conflict():
    """The failure a company brain actually has. Negation cannot see this."""
    score = conflicts_on_numbers("The Enterprise plan is 399 USD per seat per year", FACT)
    assert score is not None and score > 0.6


def test_the_same_price_is_not_a_conflict():
    assert conflicts_on_numbers(FACT, FACT) is None


def test_different_subjects_with_numbers_do_not_conflict():
    """Both contain digits; they are not about the same thing."""
    assert (
        conflicts_on_numbers(
            "Refunds are available within 30 days of purchase",
            "Critical incidents are acknowledged within 15 minutes",
        )
        is None
    )


def test_a_claim_without_numbers_never_conflicts_numerically():
    assert conflicts_on_numbers("Enterprise support is excellent", FACT) is None


def test_numbers_alone_are_not_enough():
    """A price and a retention window share no context, so no conflict."""
    assert conflicts_on_numbers("Data is deleted within 90 days", FACT) is None


# ------------------------------------------------------------------- the corpus


def test_the_corpus_is_synthetic_and_committed():
    """Rule 6: synthetic or public data only, and reproducible from the repo."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    assert DOCUMENTS, "no documents defined"
    for doc in DOCUMENTS:
        path = root / doc.path
        assert path.is_file(), f"missing committed document: {doc.path}"
        assert doc.facts, f"{doc.id} establishes no facts"
        # Synthetic company, so nothing private can be in here.
        assert COMPANY in path.read_text() or COMPANY == "Halcyon Robotics"


# --------------------------------------------------------------------- the run


def _store(suffix: str) -> GraphStore:
    base = Settings.from_env()
    return GraphStore(dataclasses.replace(base, graph=base.graph + suffix))


def _db_available() -> bool:
    try:
        return _store("_probe").ping()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_available(), reason="FalkorDB not reachable"
)


@pytest.fixture
def swarm():
    store = _store("_test_company")
    store.reset()
    store.create_indexes()
    s = Swarm(store)
    s.start("company", "s_company")
    yield s
    store.graph.delete()


def test_documents_become_kernel_facts_with_provenance(swarm):
    counts = CompanyBrain(swarm).seed()
    assert counts["documents"] == len(DOCUMENTS)
    assert swarm.kernel_stats()["facts"] == counts["facts"]

    fact = next(f for f in swarm.kernel.facts() if "Enterprise plan" in f.text)
    provenance = swarm.kernel.provenance(fact.id) or {}
    assert "pricing" in provenance.get("source", "").lower()


def test_a_wrong_price_is_caught_against_the_document(swarm):
    brain = CompanyBrain(swarm)
    brain.seed()
    brain._say("ag_sales", WRONG_PRICE)
    claim_id = swarm.store.scalar(
        "MATCH (c:Claim) WHERE c.text = $t RETURN c.id", {"t": WRONG_PRICE}
    )
    verdict = swarm.sanitizer.judge(claim_id, WRONG_PRICE)
    assert verdict.conflicts, "a 399 price must conflict with the 499 policy"


def test_the_whole_scenario_contains_the_price_and_then_the_policy_moves(swarm):
    steps = list(CompanyBrain(swarm).scenario())
    phases = [s["phase"] for s in steps]

    assert phases[0] == "seeded"
    assert "conflict" in phases and "stale" in phases
    assert phases.index("conflict") < phases.index("revised") < phases.index("stale")

    # Both the invented price and the colleague repeating it are quarantined.
    quarantined = swarm.store.rows(
        "MATCH (c:Claim) WHERE c.status = 'quarantined' RETURN c.text"
    )
    assert len(quarantined) == 2, quarantined

    # The price in force is the revised one, and the old fact is kept.
    facts = [f.text for f in swarm.kernel.facts()]
    assert any("549" in text for text in facts)
    assert not any("499" in text for text in facts), "the old price must be superseded"
    assert swarm.kernel.count() > len(facts), "history must be retained"

    # The answer that was correct before the price change is now flagged.
    stale = swarm.store.rows(
        "MATCH (c:Claim) WHERE c.text = $t RETURN c.status", {"t": OLD_PRICE}
    )
    assert stale and stale[0][0] == "suspect", stale


def test_the_documents_are_seeded_even_when_the_kernel_holds_other_facts(swarm):
    """The kernel is shared; another domain's facts must not block seeding."""
    swarm.kernel.register_fact("Mars has no breathable oxygen", trust_level="primary")
    before = swarm.kernel_stats()["facts"]

    steps = list(CompanyBrain(swarm).scenario())
    assert steps[0]["phase"] == "seeded"
    assert "Loaded" in steps[0]["message"], steps[0]["message"]
    assert swarm.kernel_stats()["facts"] > before


def test_the_scenario_is_idempotent(swarm):
    """Running it twice must not duplicate documents or facts."""
    brain = CompanyBrain(swarm)
    list(brain.scenario())
    facts_once = swarm.kernel_stats()["facts"]
    sources_once = swarm.store.scalar("MATCH (s:Source) RETURN count(s)")

    list(brain.scenario())
    # Idempotent: the documents are already loaded, and the revised price already
    # exists, so a second run must not invent more truth.
    assert swarm.kernel_stats()["facts"] == facts_once
    assert swarm.store.scalar("MATCH (s:Source) RETURN count(s)") == sources_once
