"""Unit tests for fingerprinting, normalisation and severity."""

from __future__ import annotations

from immunegraph.models import (
    fingerprint,
    normalise_text,
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
