"""Grounding claims against public knowledge.

The unit tests exercise the judging rule with no network at all, because the
rule is where the risk lives. An earlier version of this module reported "Mars
is made of cheese" as SUPPORTED by an article about Cheez-Its: the overlap
coefficient flatters short claims, and Wikipedia's search matches individual
words rather than meaning. Both failure modes are pinned here.

The end-to-end test stubs the network out entirely — a test that depends on
Wikipedia being up would be a flaky test, and the arrest mechanics are what
matter.
"""

from __future__ import annotations

import dataclasses

import pytest

from immunegraph.config import Settings
from immunegraph.db import GraphStore
from immunegraph.grounding import Evidence, WikipediaGrounder, _query_terms
from immunegraph.swarm import Swarm

JUDGE = WikipediaGrounder.judge

MARS_INTRO = (
    "Mars is the fourth planet from the Sun. "
    "The atmosphere of Mars is about 95% carbon dioxide. "
    "Mars habitats would have to contend with surface conditions that include "
    "almost no oxygen in the air. "
    "The planet is named after the Roman god of war."
)


# ------------------------------------------------------------------ the rule


def test_limiter_sentence_refutes_the_claim():
    """'Almost no oxygen' refutes 'breathable oxygen' without the word 'not'."""
    verdict, _score, reason, sentence = JUDGE(
        "Mars has breathable oxygen at night", MARS_INTRO
    )
    assert verdict == "contradicts"
    assert "oxygen" in sentence.lower()
    assert "limits" in reason


def test_negation_asymmetry_refutes_the_claim():
    verdict, *_ = JUDGE("Mars has a breathable atmosphere", "Mars has no breathable atmosphere")
    assert verdict == "contradicts"


def test_a_sentence_that_states_the_claim_supports_it():
    """Coverage, not Jaccard: a sentence saying exactly this is still long."""
    verdict, _score, _reason, sentence = JUDGE(
        "The Earth orbits the Sun",
        "Earth orbits the Sun at an average distance of 149.60 million km.",
    )
    assert verdict == "supports"
    assert "orbits the sun" in sentence.lower()


def test_unrelated_article_is_silent():
    verdict, *_ = JUDGE(
        "Mars is made of cheese",
        "Swiss cheese is a yellow medium-hard cheese with a mild nutty flavour.",
    )
    assert verdict == "silent"


def test_the_cheez_it_regression():
    """The exact pair that used to come back SUPPORTED.

    "Mars is made of cheese" once matched an article about Cheez-Its and was
    reported as corroborated, because two shared words out of three scored 0.67
    on the overlap coefficient.
    """
    cheez_it = (
        "Cheez-It is a brand of cheese cracker manufactured by the Kellanova "
        "division of Kellogg's, made with wheat flour and cheese."
    )
    verdict, *_ = JUDGE("Mars is made of cheese", cheez_it)
    assert verdict != "supports", "a cracker article must never corroborate a claim about Mars"


def test_short_claim_alone_cannot_corroborate():
    """A one-word claim must not be 'supported' by any mention of that word."""
    verdict, *_ = JUDGE("cheese", "Cheese is a dairy product made from milk.")
    assert verdict == "silent"


def test_judge_handles_an_empty_extract():
    verdict, score, reason, sentence = JUDGE("Mars has oxygen", "")
    assert verdict == "silent"
    assert score == 0.0


# ------------------------------------------------------------- query building


def test_query_terms_drop_stopwords_and_duplicates():
    assert _query_terms("Mars has breathable oxygen at night") == "mars has breathable oxygen night"
    assert _query_terms("The the THE") == "the"


def test_query_terms_respects_the_limit():
    assert len(_query_terms("alpha beta gamma delta epsilon zeta eta theta", limit=3).split()) == 3


# ------------------------------------------------------------ evidence shape


def test_fact_text_is_short_and_prefers_the_evidence_sentence():
    evidence = Evidence(
        claim="x", query="x", sentence="Venus is 96.5% carbon dioxide. It is hot.",
        extract="long extract",
    )
    fact = evidence.fact_text()
    assert fact == "Venus is 96.5% carbon dioxide"


def test_fact_text_is_none_without_evidence():
    assert Evidence(claim="x", query="x").fact_text() is None


# ------------------------------------------------------ end to end, no network


def _store(suffix: str) -> GraphStore:
    base = Settings.from_env()
    return GraphStore(dataclasses.replace(base, graph=base.graph + suffix))


def _db_available() -> bool:
    try:
        return _store("_probe").ping()
    except Exception:
        return False


class _StubGrounder:
    """Stands in for the network so the arrest mechanics can be tested."""

    def __init__(self, evidence: Evidence) -> None:
        self.evidence = evidence
        self.calls = 0

    def ground(self, claim: str) -> Evidence:
        self.calls += 1
        return self.evidence


@pytest.mark.skipif(not _db_available(), reason="FalkorDB not reachable")
def test_a_refuted_claim_is_arrested_and_teaches_the_kernel():
    store = _store("_test_grounding")
    store.reset()
    store.create_indexes()
    try:
        swarm = Swarm(store)
        swarm.grounder = _StubGrounder(
            Evidence(
                claim="Venus has a breathable atmosphere",
                query="venus breathable atmosphere",
                title="Atmosphere of Venus",
                url="https://en.wikipedia.org/wiki/Atmosphere_of_Venus",
                sentence="Venus's atmosphere is composed of 96.5% carbon dioxide.",
                verdict="contradicts",
                overlap=0.8,
                reason="the source limits what the claim asserts",
            )
        )

        result = swarm.submit_claim("Venus has a breathable atmosphere")

        assert result["accepted"]
        assert result["arrest"] is not None
        arrest = result["arrest"]
        assert arrest["quarantined"] == result["claim_id"]
        assert arrest["source_url"].startswith("https://en.wikipedia.org/")
        assert arrest["fact_id"], "a refutation must teach the kernel"

        status = store.scalar(
            "MATCH (c:Claim {id: $id}) RETURN c.status", {"id": result["claim_id"]}
        )
        assert status == "quarantined"
        assert swarm.kernel_stats()["facts"] == 1
        # The antibody cites the public source, so provenance is a traversal.
        provenance = swarm.fact_provenance(arrest["fact_id"])
        assert "Wikipedia" in (provenance or {}).get("source", "")
    finally:
        store.graph.delete()


@pytest.mark.skipif(not _db_available(), reason="FalkorDB not reachable")
def test_a_supported_claim_is_not_arrested():
    store = _store("_test_grounding_ok")
    store.reset()
    store.create_indexes()
    try:
        swarm = Swarm(store)
        swarm.grounder = _StubGrounder(
            Evidence(
                claim="The Earth orbits the Sun",
                query="earth orbits sun",
                title="Earth's orbit",
                url="https://en.wikipedia.org/wiki/Earth%27s_orbit",
                sentence="Earth orbits the Sun at an average distance of 149.60 million km.",
                verdict="supports",
                overlap=1.0,
            )
        )
        result = swarm.submit_claim("The Earth orbits the Sun")
        assert result["accepted"]
        assert result["arrest"] is None, "the swarm only quarantines what a source refutes"
        assert swarm.kernel_stats()["facts"] == 0
    finally:
        store.graph.delete()


@pytest.mark.skipif(not _db_available(), reason="FalkorDB not reachable")
def test_submitting_before_any_session_exists_works():
    """A visitor can land on a fresh deployment and submit immediately."""
    store = _store("_test_grounding_cold")
    store.reset()
    store.create_indexes()
    try:
        swarm = Swarm(store)
        assert swarm.session_id is None
        swarm.grounder = _StubGrounder(Evidence(claim="z", query="z", verdict="silent"))
        result = swarm.submit_claim("Venus has a breathable atmosphere")
        assert result["accepted"], "a cold start must not raise 'call Swarm.start() first'"
    finally:
        store.graph.delete()
