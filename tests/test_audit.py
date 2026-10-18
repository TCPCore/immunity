"""The two-sided self-audit.

These tests exist because the audit found two defects that 134 passing tests did
not: a 20% autoimmunity rate from subject-blind conflict matching, and an 82%
autoimmunity rate introduced by the naive fix for it. Both numbers are pinned
here so neither can regress silently.

The unit tests need no database. The audit tests build a small kernel and run the
real sanitiser.
"""

from __future__ import annotations

import dataclasses

import pytest

from immunegraph.audit import KernelAuditor, Probe, Report
from immunegraph.config import Settings
from immunegraph.db import GraphStore
from immunegraph.event_log import WriteLog
from immunegraph.models import same_subject, subject_head
from immunegraph.swarm import Swarm

ENTERPRISE = "The Enterprise plan costs 499 USD per seat per year"
TEAM = "The Team plan costs 99 USD per seat per year"
REFUNDS = "Refunds are available within 30 days of purchase"

FACTS = (ENTERPRISE, TEAM, REFUNDS)


# ------------------------------------------------------------ subject binding


def test_subject_head_is_the_entity():
    assert subject_head(ENTERPRISE) == "enterprise"
    assert subject_head(TEAM) == "team"
    assert subject_head(REFUNDS) == "refunds"


def test_different_entities_are_different_subjects():
    assert not same_subject(ENTERPRISE, TEAM)


def test_same_entity_with_different_wording_is_the_same_subject():
    assert same_subject(ENTERPRISE, "Enterprise seats cost 499 USD per seat per year")


def test_missing_subject_is_treated_as_matching():
    """Better to check than to silently skip a contradiction."""
    assert same_subject("", ENTERPRISE)


# -------------------------------------------------------------------- probes


def test_probes_include_both_polarities():
    probes = [
        Probe("number", "x", should_conflict=True),
        Probe("control", "y", should_conflict=False),
    ]
    assert sum(p.should_conflict for p in probes) == 1


def test_report_metrics_are_safe_when_empty():
    report = Report(probes=0, contradictions=0, controls=0, caught=0, false_alarms=0)
    assert report.coverage == 0.0
    assert report.autoimmunity == 0.0
    assert report.attribution == 0.0


def test_precision_and_recall_are_derived_not_stored():
    """Standard metrics, computed from the probe outcomes."""
    report = Report(probes=10, contradictions=4, controls=6, caught=3, false_alarms=1)
    assert report.recall == 0.75          # 3 of 4 falsehoods caught
    assert report.precision == 0.75       # of 4 flags, 3 were false
    assert report.f1 == 0.75


def test_precision_punishes_a_trigger_happy_detector():
    """Flag everything and recall is perfect while precision collapses."""
    report = Report(probes=10, contradictions=4, controls=6, caught=4, false_alarms=6)
    assert report.recall == 1.0
    assert report.precision == 0.4
    assert report.f1 < 0.6


def test_metrics_are_safe_when_nothing_flagged():
    report = Report(probes=5, contradictions=2, controls=3, caught=0, false_alarms=0)
    assert report.precision == 0.0
    assert report.recall == 0.0
    assert report.f1 == 0.0


def test_a_perfect_detector_still_reports_both_numbers():
    report = Report(probes=10, contradictions=4, controls=6, caught=4, false_alarms=0)
    assert report.coverage == 1.0
    assert report.autoimmunity == 0.0
    assert report.attribution == 1.0


def test_flagging_everything_looks_perfect_on_coverage_alone():
    """The whole reason the second number exists."""
    report = Report(probes=10, contradictions=4, controls=6, caught=4, false_alarms=6)
    assert report.coverage == 1.0, "a detector that flags everything scores full marks"
    assert report.autoimmunity == 1.0, "...and is completely useless"


# -------------------------------------------------------------- the real thing


def _store(suffix: str) -> GraphStore:
    base = Settings.from_env()
    return GraphStore(dataclasses.replace(base, graph=base.graph + suffix))


def _db_available() -> bool:
    try:
        return _store("_probe").ping()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="FalkorDB not reachable")


@pytest.fixture
def swarm():
    store = _store("_test_audit")
    store.reset()
    store.create_indexes()
    # An attached log, so the write-through property can actually be asserted.
    s = Swarm(store, log=WriteLog(":memory:"))
    s.start("audit", "s_audit")
    for fact in FACTS:
        s.kernel.register_fact(fact, trust_level="primary")
    yield s
    store.graph.delete()


def test_a_true_restatement_is_not_flagged(swarm):
    """The 20% autoimmunity bug, pinned.

    "Enterprise costs 499 USD per seat per year" is true. It was judged to
    conflict with the *Team* plan's fact, because the rule compared prices
    without checking which entity the claim was about.
    """
    verdict = swarm.sanitizer.judge("c_true", "Enterprise costs 499 USD per seat per year")
    assert not verdict.conflicts, "a true statement about Enterprise contradicted the Team fact"


def test_a_genuine_number_conflict_is_still_caught(swarm):
    verdict = swarm.sanitizer.judge(
        "c_false", "The Enterprise plan costs 399 USD per seat per year"
    )
    assert verdict.conflicts
    assert any(c["fact_id"] for c in verdict.conflicts)


def test_the_audit_scores_perfectly_on_the_labelled_set(swarm):
    """Precision and recall against generated falsehoods and hand-written truths.

    The negatives include hard ones: true restatements phrased the way a person
    would say them rather than the way the document does. Flagging those is what
    autoimmunity means in practice.
    """
    report = KernelAuditor(swarm).audit()
    assert report.recall == 1.0, "every generated contradiction should be caught"
    assert report.precision == 1.0, "and nothing true should be flagged"
    assert report.f1 == 1.0
    assert report.controls >= 3


def test_the_audit_produces_all_three_measures(swarm):
    report = KernelAuditor(swarm).audit()
    assert report.probes > 0
    assert report.controls > 0
    assert report.contradictions > 0
    assert 0.0 <= report.coverage <= 1.0
    assert report.autoimmunity == 0.0, "the defence must not flag its own facts"
    assert report.attribution == 1.0


def test_the_audit_finds_falsehoods_that_contradiction_cannot_reach(swarm):
    """A Starter plan at the Enterprise price contradicts nothing."""
    report = KernelAuditor(swarm).audit()
    assert report.uncovered, "entity substitution should be reported as uncovered"
    assert all(u["kind"] == "subject" for u in report.uncovered)


def test_naive_vaccination_would_poison_the_kernel(swarm):
    """Why the vaccine is subject-bound.

    Recording "The Starter plan costs 499 USD per seat per year" as known-false
    makes it fuzzily match the TRUE "The Enterprise plan costs 499 ..." on every
    word but one. Without subject binding that scored 82% autoimmunity, the
    vaccine attacking the host, while coverage looked perfect.
    """
    swarm.kernel.register_refuted("The Starter plan costs 499 USD per seat per year")
    verdict = swarm.sanitizer.judge("c_control", ENTERPRISE)
    assert not verdict.conflicts, "the vaccine must not flag the fact it was derived from"


def test_vaccination_closes_the_holes_without_causing_autoimmunity(swarm):
    auditor = KernelAuditor(swarm)
    before = auditor.audit()
    assert before.uncovered and before.autoimmunity == 0.0

    created = auditor.vaccinate(before)
    assert len(created) == len(before.uncovered)

    after = auditor.audit()
    assert after.uncovered == [], "every blind spot must be closed"
    assert after.coverage >= before.coverage
    assert after.autoimmunity == 0.0, "and closing them must not poison the kernel"
    assert after.attribution == 1.0


def test_a_vaccinated_statement_is_actually_caught(swarm):
    statement = "The Starter plan costs 499 USD per seat per year"
    assert not swarm.sanitizer.judge("c1", statement).conflicts

    swarm.kernel.register_refuted(statement, reason="not a product we offer")
    verdict = swarm.sanitizer.judge("c2", statement)
    assert verdict.conflicts, "negative memory must catch a repeat"


def test_negative_memory_is_recorded_as_a_logged_write(swarm):
    """Everything else in this system is replayable, so this must be too."""
    before = swarm.log_count()
    swarm.kernel.register_refuted("Some statement", reason="audit")
    assert swarm.log_count() > before
