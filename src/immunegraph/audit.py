"""Self-audit: measure what the defence catches AND what it wrongly catches.

Every system of this kind reports one number, how much it detects. That number
is gameable: a detector that flags everything scores 100%. The only honest
measurement is two-sided, and the side nobody reports is the one that bites.

That side has a name in the literature. The Agent-Native Immune System paper
(arXiv:2606.28270) lists the **Autoimmunity Rate**, the false-positive
intervention rate, among the field's open challenges and does not implement it.
This module does, and pairs it with coverage so neither can be optimised alone.

It found a real defect immediately. With 134 tests passing, the audit measured a
**20% autoimmunity rate**: "Enterprise costs 499 USD per seat per year", a true
statement, was judged to *conflict*, because it numerically mismatches the
*Team* plan's fact. The rule was subject-blind: it compared prices without
checking which entity the claim was about. Binding conflicts to a shared subject
took autoimmunity to 0% with coverage unchanged.

A third measure follows from the same reasoning. A mutation can be caught for
the wrong reason, flagged against a fact it has nothing to do with. **Attribution
accuracy** asks not "did it fire" but "did it fire at the right target", because a
defence that catches the right answer by accident will fail the next time.

And some falsehoods are unreachable by contradiction at all. "The Starter plan
costs 499 USD" contradicts nothing Halcyon holds true; what is missing is the
knowledge that a Starter plan does not exist. The audit reports that surface
separately instead of hiding it, and `vaccinate()` closes it by recording the
statement as known-false.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from .models import fingerprint

_NUMBER = re.compile(r"\d+")
_ARTICLE = re.compile(r"^(the|a|an)\s+", re.IGNORECASE)

# Entities the audited corpus does not contain. Substituting one produces a
# statement that is plausible, unpriced by any source, and true of nothing.
UNKNOWN_ENTITIES = ("Starter", "Basic", "Legacy", "Partner", "Trial")

# Hand-labelled TRUE statements about the bundled corpus, phrased the way a
# person would phrase them rather than the way the document does. These are the
# negatives that make precision mean something: a detector that flags "customers
# can request a refund within 30 days of buying" against "Refunds are available
# within 30 days of purchase" is too blunt to put in front of a customer.
HARD_NEGATIVES: tuple[str, ...] = (
    "Enterprise seats are billed at 499 USD per seat each year",
    "The Enterprise tier costs 499 USD per seat per year",
    "Customers can request a refund within 30 days of buying",
    "Critical incidents are picked up within 15 minutes",
    "EU customer telemetry stays in the EU",
    "A Team seat is 99 USD for the year",
)


@dataclass(frozen=True)
class Probe:
    """One generated test statement."""

    kind: str
    text: str
    fact_id: str | None = None
    should_conflict: bool = False


@dataclass
class Report:
    """The three-sided result."""

    probes: int
    contradictions: int
    controls: int
    caught: int
    false_alarms: int
    uncovered: list[dict] = field(default_factory=list)
    misattributed: list[dict] = field(default_factory=list)
    # Every probe and what happened to it. The UI renders this rather than a
    # hardcoded summary, so what is on screen is what was measured.
    outcomes: list[dict] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)
    elapsed_ms: float = 0.0

    @property
    def coverage(self) -> float:
        return self.caught / self.contradictions if self.contradictions else 0.0

    @property
    def autoimmunity(self) -> float:
        return self.false_alarms / self.controls if self.controls else 0.0

    @property
    def precision(self) -> float:
        """Of everything flagged, how much was actually false."""
        flagged = self.caught + self.false_alarms
        return self.caught / flagged if flagged else 0.0

    @property
    def recall(self) -> float:
        """Of everything false, how much was flagged."""
        return self.coverage

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def attribution(self) -> float:
        if not self.caught:
            return 0.0
        return (self.caught - len(self.misattributed)) / self.caught

    def summary(self) -> dict:
        return {
            "probes": self.probes,
            "coverage": round(self.coverage, 3),
            "precision": round(self.precision, 3),
            "recall": round(self.recall, 3),
            "f1": round(self.f1, 3),
            "autoimmunity": round(self.autoimmunity, 3),
            "attribution": round(self.attribution, 3),
            "caught": self.caught,
            "contradictions": self.contradictions,
            "false_alarms": self.false_alarms,
            "controls": self.controls,
            "uncovered": len(self.uncovered),
            "misattributed": len(self.misattributed),
            "elapsed_ms": round(self.elapsed_ms, 1),
        }


class KernelAuditor:
    """Generates adversarial probes from the kernel and measures the defence."""

    def __init__(self, swarm) -> None:
        self.swarm = swarm
        self.store = swarm.store
        self.kernel = swarm.kernel
        self.sanitizer = swarm.sanitizer

    # ------------------------------------------------------------------ probes

    def probes(self) -> list[Probe]:
        facts = self.kernel.facts()
        probes: list[Probe] = []

        for index, fact in enumerate(facts):
            numbers = _NUMBER.findall(fact.text)

            # 1. Perturb this fact's own number. A straight contradiction.
            if numbers:
                shifted = self._shift(fact.text, numbers[0])
                if shifted:
                    probes.append(Probe("number", shifted, fact.id, should_conflict=True))

            # 2. Borrow a different fact's number. The classic cross-wiring error.
            others = [f for f in facts if f.id != fact.id]
            if numbers and others and _NUMBER.search(others[0].text):
                borrowed = _NUMBER.search(others[0].text).group(0)
                if borrowed != numbers[0]:
                    probes.append(
                        Probe(
                            "transplant",
                            fact.text.replace(numbers[0], borrowed, 1),
                            fact.id,
                            should_conflict=True,
                        )
                    )

            # 3. Substitute an entity the corpus never mentions. This contradicts
            #    nothing, which is exactly the point: it is unreachable by
            #    comparison, so it is reported as uncovered rather than counted
            #    as a detection failure.
            swapped = self._swap_subject(fact.text, index)
            if swapped:
                probes.append(Probe("subject", swapped, fact.id, should_conflict=False))

            # 4. Controls: the fact itself and truth-preserving rewrites of it.
            #    Flagging any of these is the immune system attacking the host.
            probes.append(Probe("control", fact.text, fact.id, should_conflict=False))
            paraphrase = self._paraphrase(fact.text)
            if paraphrase:
                probes.append(Probe("paraphrase", paraphrase, fact.id, should_conflict=False))

            # 5. A back-reference: a claim built on a fact must not be flagged.
            if numbers:
                probes.append(
                    Probe(
                        "reference",
                        f"According to our records, {_ARTICLE.sub('', fact.text).lower()}",
                        fact.id,
                        should_conflict=False,
                    )
                )

        # Hand-labelled negatives, included once rather than per fact.
        for statement in HARD_NEGATIVES:
            probes.append(Probe("hard_negative", statement, None, should_conflict=False))

        return probes

    @staticmethod
    def _shift(text: str, number: str) -> str | None:
        value = int(number)
        # Pick a visibly different value that no other fact uses.
        delta = 100 if value >= 200 else 10
        new = value - delta if value - delta > 0 else value + delta
        return text.replace(number, str(new), 1)

    @staticmethod
    def _swap_subject(text: str, index: int) -> str | None:
        entity = UNKNOWN_ENTITIES[index % len(UNKNOWN_ENTITIES)]
        stripped = _ARTICLE.sub("", text)
        words = stripped.split()
        if not words:
            return None
        # Replace the leading entity, keeping the rest of the sentence intact.
        return f"The {entity} " + " ".join(words[1:])

    @staticmethod
    def _paraphrase(text: str) -> str | None:
        """A rewrite that must not change truth or trigger a conflict."""
        out = _ARTICLE.sub("", text)
        out = out.replace(" costs ", " is priced at ")
        out = out.replace(" is available ", " is offered ")
        return out if out != text else None

    # ------------------------------------------------------------------- audit

    def audit(self) -> Report:
        started = time.perf_counter()
        facts = self.kernel.facts()
        probes = self.probes()

        report = Report(
            probes=len(probes),
            contradictions=sum(1 for p in probes if p.should_conflict),
            controls=sum(
               1 for p in probes
               if p.kind in {"control", "paraphrase", "reference", "hard_negative"}
            ),
            caught=0,
            false_alarms=0,
        )

        for index, probe in enumerate(probes):
            verdict = self.sanitizer.judge(f"probe_{index:03d}", probe.text, facts)
            flagged = bool(verdict.conflicts)

            report.outcomes.append(
                {
                    "kind": probe.kind,
                    # A subject mutation is neither: it is unreachable by
                    # contradiction, so "clean" would misreport it after
                    # vaccination when it is correctly flagged.
                    "expected": (
                        "conflict"
                        if probe.should_conflict
                        else ("unverified" if probe.kind == "subject" else "clean")
                    ),
                    "result": "flagged" if flagged else "clean",
                    "text": probe.text,
                }
            )

            if probe.should_conflict:
                if flagged:
                    report.caught += 1
                    # Caught, but against the wrong fact? That is luck, not
                    # detection, and it will not survive a different corpus.
                    against = {c["fact_id"] for c in verdict.conflicts}
                    if probe.fact_id not in against:
                        report.misattributed.append(
                            {"kind": probe.kind, "text": probe.text,
                             "expected": probe.fact_id, "conflicted_with": sorted(against)}
                        )
                else:
                    report.failures.append(
                        {"kind": probe.kind, "text": probe.text, "fact_id": probe.fact_id}
                    )
            else:
                if flagged and probe.kind in {"control", "paraphrase", "reference", "hard_negative"}:
                    report.false_alarms += 1
                elif not flagged and probe.kind == "subject":
                    # Unreachable by contradiction: a genuine coverage hole.
                    report.uncovered.append(
                        {"kind": probe.kind, "text": probe.text, "fact_id": probe.fact_id}
                    )

        report.elapsed_ms = (time.perf_counter() - started) * 1000.0
        return report

    # --------------------------------------------------------------- repairing

    def vaccinate(self, report: Report) -> list[str]:
        """Record uncovered statements as known-false, then re-measure.

        This is what makes the metric a loop rather than a score. A blind spot
        that is merely reported is a bug report; one that is closed and then
        re-measured is immunity.
        """
        created: list[str] = []
        for item in report.uncovered:
            created.append(
                self.kernel.register_refuted(
                    item["text"],
                    fact_id=item["fact_id"],
                    reason="unreachable by contradiction; recorded by self-audit",
                )
            )
        return created
