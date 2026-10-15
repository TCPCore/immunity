"""Unit tests for the deterministic rule extractor."""

from __future__ import annotations

import pytest

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


# ------------------------------------------------------------ mode selection


def test_rule_mode_builds_the_rule_extractor(monkeypatch):
    from immunegraph.extractor import RuleExtractor, get_extractor

    monkeypatch.setenv("EXTRACTOR", "rule")
    assert isinstance(get_extractor(), RuleExtractor)


def test_auto_mode_is_the_default(monkeypatch):
    from immunegraph.extractor import AutoExtractor, get_extractor

    monkeypatch.delenv("EXTRACTOR", raising=False)
    assert isinstance(get_extractor(), AutoExtractor)


def test_llm_mode_without_a_key_fails_fast(monkeypatch):
    """Previously this built fine and then raised a raw 401 mid-conversation."""
    from immunegraph.extractor import get_extractor

    monkeypatch.setenv("EXTRACTOR", "llm")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        get_extractor()


def test_llm_mode_with_a_key_builds(monkeypatch):
    from immunegraph.extractor import LLMExtractor, get_extractor

    monkeypatch.setenv("EXTRACTOR", "llm")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-a-real-key")
    assert isinstance(get_extractor(), LLMExtractor)


def test_typo_in_mode_is_rejected_not_silently_downgraded(monkeypatch):
    """EXTRACTOR=rules used to fall through to auto without a word."""
    from immunegraph.extractor import get_extractor

    monkeypatch.setenv("EXTRACTOR", "rules")
    with pytest.raises(ValueError, match="EXTRACTOR must be one of"):
        get_extractor()
