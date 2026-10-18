"""The kernel of truth.

KernelFacts are canonical, source-backed statements the swarm holds as true.
They complement the Infection/Claim machinery rather than duplicating it:

    Infection + quarantined Claim  =  memory of what was WRONG
    KernelFact                     =  memory of what is RIGHT

That distinction is what lets the antibody *teach*. Before the kernel, an
outbreak left a scar: the false claim was quarantined and everyone's trust
dropped, but the swarm learned nothing it could reuse. With the kernel, the
antibody's correction becomes a fact that every future claim is judged against,
so each outbreak makes the swarm permanently harder to fool.

Deliberately graph-native. A fact's provenance, which antibody established it,
citing which source, and its conflicts are edges, and sanitisation is a
traversal. Extracting the kernel into a side table would leave those queries
with nowhere to run, which is precisely what hackathon rule 3 forbids.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from .memory import MemoryWriter
from .models import RefutedStatement
from .queries import K1_KERNEL_FACTS, K2_KERNEL_CONFLICTS, K3_KERNEL_STATS, K5_FACT_PROVENANCE
from .schema import KERNEL_ACTIVE, TRUST_PRIMARY


def new_fact_id() -> str:
    """Fact ids are opaque on purpose: the event log, not the id, carries
    identity across a replay."""
    return f"kf_{uuid.uuid4().hex[:10]}"


@dataclass
class Fact:
    id: str
    text: str
    trust_level: str
    version: int
    created_at: str
    source: str | None = None


class Kernel:
    """Reads and writes the canonical set of facts the swarm holds as true."""

    def __init__(self, store, writer: MemoryWriter | None = None) -> None:
        self.store = store
        self.writer = writer or MemoryWriter(store)

    # ------------------------------------------------------------------ write

    def register_fact(
        self,
        text: str,
        source=None,
        trust_level: str = TRUST_PRIMARY,
        antibody_id: str | None = None,
        fact_id: str | None = None,
    ) -> str:
        """Add a fact to the kernel, optionally attributing it to an antibody.

        ``source`` may be a Source instance or a bare source id. Passing the
        antibody id records *who* taught the swarm this, which is the edge the
        provenance query walks.
        """
        fact_id = fact_id or new_fact_id()
        self.writer.record_kernel_fact(fact_id, text, trust_level=trust_level)

        if source is not None:
            source_id = source if isinstance(source, str) else source.id
            self.writer.link_fact_source(fact_id, source_id)

        if antibody_id:
            self.writer.link_antibody_establishes(antibody_id, fact_id)

        return fact_id

    def supersede(self, old_fact_id: str, new_fact_id: str) -> None:
        """Retire a fact in favour of a newer one. History is kept."""
        self.writer.supersede_kernel_fact(old_fact_id, new_fact_id)

    # ------------------------------------------------------------------- read

    def facts(self) -> list[Fact]:
        """Every active fact. One query, sanitisation reuses this list."""
        return [
            Fact(
                id=row[0],
                text=row[1] or "",
                trust_level=row[2] or "unknown",
                version=int(row[3] or 1),
                created_at=row[4] or "",
                source=row[5],
            )
            for row in self.store.rows(K1_KERNEL_FACTS)
        ]

    def conflicts(self) -> list[dict]:
        return [
            {
                "claim_id": r[0], "claim": r[1], "claim_status": r[2],
                "fact_id": r[3], "fact": r[4], "trust_level": r[5],
                "overlap": float(r[6] or 0),
            }
            for r in self.store.rows(K2_KERNEL_CONFLICTS)
        ]

    def stats(self) -> dict:
        row = self.store.one(K3_KERNEL_STATS)
        if not row:
            return {"facts": 0, "aligned_edges": 0, "conflict_edges": 0}
        return {
            "facts": int(row[0] or 0),
            "aligned_edges": int(row[1] or 0),
            "conflict_edges": int(row[2] or 0),
        }

    def provenance(self, fact_id: str) -> dict | None:
        row = self.store.one(K5_FACT_PROVENANCE, {"fact_id": fact_id})
        if not row:
            return None
        return {
            "fact_id": row[0], "text": row[1], "trust_level": row[2],
            "created_at": row[3], "established_by": row[4],
            "source": row[5], "source_url": row[6],
            "related_infections": [x for x in (row[7] or []) if x],
        }

    # ------------------------------------------------------------- negative

    def register_refuted(
        self, text: str, fact_id: str | None = None, reason: str = ""
    ) -> str:
        """Record a statement known to be false. Kept beside the facts, not in them."""
        statement = RefutedStatement(
            id=f"rf_{uuid.uuid4().hex[:10]}",
            text=text,
            fact_id=fact_id,
            reason=reason,
        )
        return self.writer.record_refuted_statement(statement)

    def refuted(self) -> list[RefutedStatement]:
        rows = self.store.rows(
            "MATCH (r:RefutedStatement) "
            "RETURN r.id, r.text, r.fact_id, r.reason, r.created_at"
        )
        return [
            RefutedStatement(
                id=row[0], text=row[1], fact_id=row[2] or None,
                reason=row[3] or "", created_at=row[4] or "",
            )
            for row in rows
        ]

    def is_empty(self) -> bool:
        return int(self.store.scalar("MATCH (k:KernelFact) RETURN count(k)") or 0) == 0

    def count(self) -> int:
        return int(self.store.scalar("MATCH (k:KernelFact) RETURN count(k)") or 0)
