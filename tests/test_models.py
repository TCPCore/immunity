"""Unit tests for fingerprinting, normalisation and severity."""

from __future__ import annotations

from immunegraph.models import (
    CONTRADICTION_OVERLAP,
    conflicts_with_fact,
    fingerprint,
    normalise_text,
    overlap_coefficient,
    severity_from_reach,
    similarity,
    token_set,
)

MARS_FALSE = "Mars has breathable oxygen at night."
MARS_PARAPHRASE = "Mars has oxygen at night."


def test_normalise_strips_stopwords_and_punctuation():
    assert normalise_text("Mars has breathable oxygen at night.") == "mars breathable oxygen night"


def test_normalise_expands_contractions_so_negation_survives():
    # Without contraction expansion "don't" tokenises to "don t" and the
    # negation marker is lost, which silently breaks contradiction detection.
    tokens = token_set("we don't need helmets")
    assert "not" in tokens
    assert "don" not in tokens


def test_fingerprint_is_stable_and_order_sensitive():
    assert fingerprint(MARS_FALSE) == fingerprint("mars has breathable oxygen at night")
    # Word order matters, so these must not collide.
    assert fingerprint("Mars has oxygen") != fingerprint("oxygen has Mars")


def test_fingerprint_differs_for_paraphrase():
    # Exact recall is deliberately strict; paraphrase is Q7's job.
    assert fingerprint(MARS_FALSE) != fingerprint(MARS_PARAPHRASE)


def test_similarity_catches_the_demo_paraphrase():
    score = similarity(MARS_FALSE, MARS_PARAPHRASE)
    assert score == 0.75, score
    assert score >= 0.40  # above the recall threshold used by immune.py


def test_similarity_is_symmetric_and_bounded():
    assert similarity(MARS_FALSE, MARS_PARAPHRASE) == similarity(MARS_PARAPHRASE, MARS_FALSE)
    assert similarity("", "anything") == 0.0


def test_severity_is_a_function_of_graph_reach():
    # 1 for the root + 1 per downstream claim + 1 per exposed agent.
    assert severity_from_reach(2, 3) == 6.0
    assert severity_from_reach(0, 0) == 1.0


# ---------------------------------------------------------- kernel matching

MARS_FACT = "Mars has no breathable oxygen"


def test_overlap_coefficient_ignores_length_difference():
    """Jaccard divides by the union, so a long fact dilutes a short claim.

    The overlap coefficient exists precisely so that a terse claim is not
    drowned out when judged against a longer canonical fact.
    """
    long_fact = (
        "NASA the Martian atmosphere is about 95 carbon dioxide with trace oxygen "
        "so there is no breathable oxygen on Mars at any time"
    )
    assert overlap_coefficient(MARS_PARAPHRASE, long_fact) > similarity(MARS_PARAPHRASE, long_fact)
    assert overlap_coefficient(MARS_PARAPHRASE, long_fact) >= 0.6


def test_conflicts_with_fact_catches_the_demo_paraphrase():
    """The headline beat: a paraphrase is recognised against the kernel."""
    score = conflicts_with_fact(MARS_PARAPHRASE, MARS_FACT)
    assert score is not None, "the paraphrase should conflict with the fact"
    assert score >= 0.60


def test_conflicts_with_fact_ignores_agreement():
    assert conflicts_with_fact("Mars has no breathable oxygen", MARS_FACT) is None


def test_conflicts_with_fact_ignores_unrelated_statements():
    assert conflicts_with_fact("The budget is late", MARS_FACT) is None


def test_conflicts_with_fact_ignores_matching_negation():
    assert conflicts_with_fact("Mars has no atmosphere", MARS_FACT) is None


def test_overlap_saves_the_case_jaccard_misses():
    """The honest version of this regression guard.

    Against a *canonical* short fact, Jaccard already suffices. The overlap
    coefficient earns its place against a prose-length fact, which is exactly
    what the LLM path produces, since it has no hand-written short form to
    compare against. There, Jaccard dilutes below threshold and misses a real
    contradiction.
    """
    prose_fact = (
        "NASA the Martian atmosphere is about 95 carbon dioxide with only trace oxygen "
        "so there is no breathable oxygen on Mars at any time of day"
    )
    assert similarity(MARS_PARAPHRASE, prose_fact) < CONTRADICTION_OVERLAP, (
        "precondition: Jaccard dilutes this pair below the contradiction threshold"
    )
    assert conflicts_with_fact(MARS_PARAPHRASE, prose_fact) is not None, (
        "the overlap coefficient should still recognise the contradiction"
    )
