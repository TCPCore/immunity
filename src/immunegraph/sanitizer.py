"""The sanitiser: judging new claims against the kernel of truth.

The division of labour is the point of the whole design:

    detection (Q1-Q5)  finds lies the swarm has never seen before, by walking
                       the support structure. It is expensive: it needs the
                       extraction step and a full scan.
    sanitisation (K1-K3) recognises lies the swarm has already learned about, by
                       matching against the kernel. It is cheap: one query for
                       the facts, then a lexical comparison per fact. No model
                       call, no cascade scan.

That second path is what "the swarm develops immunity" means in practice, and it
is measurable — every verdict carries its own elapsed time so the fast path can
be compared against the slow one instead of merely asserted.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .kernel import Fact, Kernel
from .memory import MemoryWriter
from .models import ALIGNMENT_OVERLAP, conflicts_with_fact, overlap_coefficient
from .queries import K4_UNSANITISED_CLAIMS
from .schema import (
    VERDICT_CLEAN,
    VERDICT_CONFLICTS,
    VERDICT_UNKNOWN,
)


@dataclass
class Verdict:
    claim_id: str
    verdict: str
    elapsed_ms: float
    conflicts: list[dict] = field(default_factory=list)
    aligned: list[dict] = field(default_factory=list)

    @property
    def best_conflict(self) -> dict | None:
        return self.conflicts[0] if self.conflicts else None

    def describe(self) -> str:
        if self.verdict == VERDICT_CONFLICTS and self.best_conflict:
            return (
                f"conflicts with kernel fact '{self.best_conflict['fact'][:64]}' "
                f"(overlap {self.best_conflict['overlap']:.2f}, "
                f"{self.elapsed_ms:.1f}ms)"
            )
        if self.verdict == VERDICT_CLEAN:
            return f"agrees with the kernel ({self.elapsed_ms:.1f}ms)"
        return f"kernel has nothing to say ({self.elapsed_ms:.1f}ms)"


class Sanitizer:
    """Judges claims against the kernel and records the verdicts as edges."""

    def __init__(self, store, writer: MemoryWriter | None = None, kernel: Kernel | None = None) -> None:
        self.store = store
        self.writer = writer or MemoryWriter(store)
        self.kernel = kernel or Kernel(store, self.writer)
        # Counting the fast path is how we can prove immunity rather than claim it.
        self.fast_path_hits = 0
        self.total_ms = 0.0

    # ------------------------------------------------------------------ judge

    def judge(self, claim_id: str, text: str, facts: list[Fact] | None = None) -> Verdict:
        """Compare one claim against the kernel and persist the result.

        A claim that contradicts the kernel is downgraded to 'suspect' straight
        away. We already hold a sourced fact that says otherwise, so the claim
        does not need a cascade scan to be judged — that is the entire saving.
        """
        started = time.perf_counter()
        facts = self.kernel.facts() if facts is None else facts

        conflicts: list[dict] = []
        aligned: list[dict] = []
        for fact in facts:
            # Score with the overlap coefficient: a terse claim judged against a
            # longer canonical fact would otherwise be diluted by Jaccard and
            # missed entirely.
            score = overlap_coefficient(text, fact.text)
            conflict_score = conflicts_with_fact(text, fact.text)
            if conflict_score is not None:
                conflicts.append(
                    {
                        "fact_id": fact.id,
                        "fact": fact.text,
                        "trust_level": fact.trust_level,
                        "overlap": conflict_score,
                    }
                )
            elif score >= ALIGNMENT_OVERLAP:
                aligned.append({"fact_id": fact.id, "fact": fact.text, "overlap": score})

        conflicts.sort(key=lambda c: c["overlap"], reverse=True)
        aligned.sort(key=lambda a: a["overlap"], reverse=True)

        if conflicts:
            verdict = VERDICT_CONFLICTS
            top = conflicts[0]
            self.writer.link_claim_conflicts_with(claim_id, top["fact_id"], top["overlap"])
            self.writer.set_claim_verdict(claim_id, verdict)
            # Never clobber a stronger verdict: detection may already have
            # quarantined this claim, and 'quarantined' outranks 'suspect'.
            self.writer.mark_claim_suspect_if_active(claim_id)
            self.fast_path_hits += 1
        elif aligned:
            verdict = VERDICT_CLEAN
            top = aligned[0]
            self.writer.link_claim_aligned_with(claim_id, top["fact_id"], top["overlap"])
            self.writer.set_claim_verdict(claim_id, verdict)
        else:
            verdict = VERDICT_UNKNOWN
            self.writer.set_claim_verdict(claim_id, verdict)

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self.total_ms += elapsed_ms
        return Verdict(
            claim_id=claim_id,
            verdict=verdict,
            elapsed_ms=elapsed_ms,
            conflicts=conflicts,
            aligned=aligned,
        )

    # ------------------------------------------------------------------ sweep

    def sanitize_pending(self, limit: int = 100) -> list[Verdict]:
        """Judge every active claim the kernel has not yet ruled on.

        The fact list is fetched once for the whole pass — that is what keeps the
        fast path cheap.
        """
        facts = self.kernel.facts()
        if not facts:
            return []
        rows = self.store.rows(K4_UNSANITISED_CLAIMS, {"limit": limit})
        return [self.judge(row[0], row[1] or "", facts=facts) for row in rows]

    def rejudge_unknown(self, limit: int = 200) -> list[Verdict]:
        """Re-judge claims the kernel previously had nothing to say about.

        Called after an antibody teaches, because a fact that arrives now can
        condemn a claim that was merely unsupported before. Claims already
        quarantined or marked suspect are skipped — their state is stronger.
        """
        facts = self.kernel.facts()
        if not facts:
            return []
        rows = self.store.rows(
            """
            MATCH (c:Claim)
            WHERE c.verdict = 'unknown' AND c.status = 'active'
            RETURN c.id, c.text
            LIMIT $limit
            """,
            {"limit": limit},
        )
        return [self.judge(row[0], row[1] or "", facts=facts) for row in rows]

    # ------------------------------------------------------------------ stats

    def summary(self) -> dict:
        return {
            "fast_path_hits": self.fast_path_hits,
            "total_ms": round(self.total_ms, 2),
            "avg_ms": round(self.total_ms / self.fast_path_hits, 2) if self.fast_path_hits else 0.0,
        }
