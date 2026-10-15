"""Unit tests for the deterministic rule extractor."""

from __future__ import annotations

from immunegraph.extractor import (
    RuleExtractor,
    detect_contradiction,
    find_sources,
    looks_like_claim,
)

MARS_FALSE = "Mars has breathable oxygen at night."
ANALYST = "If Mars has breathable oxygen, we don't need helmets."
PLANNER = "We can skip life support."


def test_extracts_a_bare_assertion_with_no_source():
    extraction = RuleExtractor().extract(MARS_FALSE)
    assert len(extraction.claims) == 1
    claim = extraction.claims[0]
    assert claim.derived_from == []
    assert claim.sources == []


def test_derivation_cue_links_to_prior_context():
    extraction = RuleExtractor().extract(ANALYST, prior_claims=[MARS_FALSE])
    assert len(extraction.claims) == 1
    assert extraction.claims[0].derived_from == [-1]


def test_conditional_is_not_reported_as_a_contradiction_of_its_premise():
    """Regression test for a real false positive.

    "If X, we don't need helmets" negates something downstream of X. Lexical
    negation detection read it as contradicting X, which produced a spurious
    outbreak rooted at the wrong claim.
    """
    extraction = RuleExtractor().extract(ANALYST, prior_claims=[MARS_FALSE])
    assert extraction.contradictions == []


def test_planner_line_derives_from_prior_context():
    extraction = RuleExtractor().extract(PLANNER, prior_claims=[ANALYST])
    assert extraction.claims[0].derived_from == [-1]
    assert extraction.contradictions == []


def test_cited_source_is_detected_with_trust_level():
    sources = find_sources("NASA data: Mars is 95% CO2 with no breathable oxygen.")
    assert sources, "expected NASA to be detected"
    nasa = sources[0]
    assert nasa.name.lower().startswith("nasa")
    assert nasa.trust_level == "primary"


def test_unknown_source_is_not_granted_trust():
    sources = find_sources("According to my cousin, the moon is made of cheese.")
    assert sources
    assert all(s.trust_level == "unknown" for s in sources)


def test_genuine_contradiction_is_still_detected():
    reason = detect_contradiction(
        "Mars has breathable oxygen at night",
        "Mars has no breathable oxygen at night",
    )
    assert reason is not None
    assert "negation" in reason


def test_agreement_is_not_a_contradiction():
    assert detect_contradiction("Mars is cold", "Mars is very cold") is None
    # Two negations agreeing with each other.
    assert detect_contradiction("Mars has no oxygen", "Mars has no atmosphere") is None


def test_unrelated_claims_are_not_contradictions():
    assert detect_contradiction("Mars is cold", "The budget is late") is None


def test_questions_and_filler_are_not_claims():
    assert not looks_like_claim("What about the oxygen?")
    assert not looks_like_claim("Hi")
    assert looks_like_claim(MARS_FALSE)
