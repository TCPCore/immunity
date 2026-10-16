"""Detection: the immune system's surveillance pass.

Each scanner runs one of the load-bearing Cypher queries, decides whether the
result constitutes an infection, and if so materialises an ``Infection`` node
whose severity comes from graph reach (Q4) rather than from any model's
opinion.

Deduplication matters: scanning is idempotent, so re-running it does not pile
up duplicate outbreaks for the same claim.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from .memory import MemoryWriter
from .models import Antibody, Infection, severity_from_reach, similarity, utcnow
from .queries import (
    Q1_CIRCULAR_SUPPORT,
    Q2_UNSUPPORTED_CLAIMS,
    Q3_CONTRADICTIONS,
    Q4_CASCADE_REACH,
    Q6_IMMUNITY_RECALL,
    Q7B_RECALL_CANDIDATES,
    Q7_VECTOR_RECALL,
)
from .schema import (
    INFECTION_ACTIVE,
    INFECTION_CIRCULAR,
    INFECTION_CONTAINED,
    INFECTION_CONTRADICTION,
    INFECTION_REPEAT,
    INFECTION_UNSUPPORTED,
)

# Cosine distance below which a paraphrase is considered the same claim.
# FalkorDB yields cosine distance, so lower is more similar.
VECTOR_MATCH_MAX_DISTANCE = 0.35

# Jaccard threshold for the dependency-free paraphrase fallback.
SIMILARITY_MATCH_MIN = 0.40

# How much trust each unresolved infection costs its associated agents.
TRUST_DECAY_PER_INFECTION = 0.15


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


@dataclass
class RecallResult:
    """Outcome of an immunity recall check."""

    hit: bool = False
    method: str = "none"          # fingerprint | vector | similarity | none
    past_claim_id: str | None = None
    past_text: str | None = None
    past_status: str | None = None
    infection_id: str | None = None
    infection_type: str | None = None
    first_seen: str | None = None
    contained_at: str | None = None
    score: float | None = None

    def describe(self) -> str:
        if not self.hit:
            return "no prior infection matches"
        detail = f"matched by {self.method}"
        if self.score is not None:
            detail += f" (score {self.score:.3f})"
        article = "an" if (self.infection_type or "")[:1].lower() in "aeiou" else "a"
        return (
            f"{detail}: {self.past_text!r} was {self.past_status} after {article} "
            f"{self.infection_type} infection first seen {self.first_seen}"
        )


class ImmuneScanner:
    """Runs the detection queries and records what it finds."""

    def __init__(self, store, writer: MemoryWriter | None = None) -> None:
        self.store = store
        self.writer = writer or MemoryWriter(store)

    # ------------------------------------------------------------ dedupe

    def has_open_infection(self, claim_id: str, infection_type: str) -> bool:
        found = self.store.scalar(
            """
            MATCH (i:Infection)-[:TARGETS]->(c:Claim {id: $cid})
            WHERE i.type = $type AND i.status IN ['active', 'contained']
            RETURN count(i)
            """,
            {"cid": claim_id, "type": infection_type},
        )
        return bool(found)

    # ---------------------------------------------------------- severity

    def cascade_reach(self, root_claim_id: str) -> tuple[int, int]:
        """(infected_claims, exposed_agents) straight from query Q4."""
        row = self.store.one(Q4_CASCADE_REACH, {"root_claim_id": root_claim_id})
        if not row:
            return 0, 0
        return int(row[2] or 0), int(row[3] or 0)

    def downstream_claims(self, root_claim_id: str) -> list[str]:
        rows = self.store.rows(
            """
            MATCH (down:Claim)-[:DERIVED_FROM*1..5]->(root:Claim {id: $root})
            RETURN DISTINCT down.id
            """,
            {"root": root_claim_id},
        )
        return [r[0] for r in rows]

    def _record(
        self,
        claim_id: str,
        infection_type: str,
        spread_to_root: bool = True,
    ) -> Infection:
        """Create an Infection, compute severity from reach, mark the cascade."""
        infected, exposed = self.cascade_reach(claim_id)
        infection = Infection(
            id=_new_id("inf"),
            type=infection_type,
            status=INFECTION_ACTIVE,
            severity=severity_from_reach(infected, exposed),
            detected_at=utcnow(),
        )
        self.writer.record_infection(infection)
        self.writer.link_infection_target(infection.id, claim_id)

        if spread_to_root:
            # Materialise the cascade so the dashboard can show the blast radius
            # without re-walking the graph on every render.
            for downstream_id in self.downstream_claims(claim_id):
                self.writer.link_infection_spread(infection.id, downstream_id)

        return infection

    # ---------------------------------------------------------- scanners

    def scan_unsupported(self) -> list[Infection]:
        """Q2: active claims with no trusted source and no derivation."""
        infections: list[Infection] = []
        for row in self.store.rows(Q2_UNSUPPORTED_CLAIMS):
            claim_id = row[0]
            if self.has_open_infection(claim_id, INFECTION_UNSUPPORTED):
                continue
            infections.append(self._record(claim_id, INFECTION_UNSUPPORTED))
        return infections

    def scan_cycles(self) -> list[Infection]:
        """Q1: cycles in the support structure — self-justifying belief.

        A cycle of length n is reported once from each of its n members, so the
        result is deduplicated by the set of claim ids in each path.
        """
        infections: list[Infection] = []
        seen: set[frozenset[str]] = set()

        rows = self.store.rows(Q1_CIRCULAR_SUPPORT)
        for row in rows:
            claim_id = row[0]
            path_texts = row[2] or []
            key = frozenset(path_texts)
            if key in seen:
                continue
            seen.add(key)
            if self.has_open_infection(claim_id, INFECTION_CIRCULAR):
                continue
            infections.append(self._record(claim_id, INFECTION_CIRCULAR, spread_to_root=False))
        return infections

    def scan_contradictions(self) -> list[Infection]:
        """Q3: two asserting agents holding incompatible beliefs."""
        infections: list[Infection] = []
        for row in self.store.rows(Q3_CONTRADICTIONS):
            claim_a = row[0]
            if self.has_open_infection(claim_a, INFECTION_CONTRADICTION):
                continue
            infections.append(self._record(claim_a, INFECTION_CONTRADICTION, spread_to_root=False))
        return infections

    def scan_all(self) -> list[Infection]:
        """Run every scanner. Order matters: unsupported roots first, so that
        the cascade they seed is already in the graph when cycles are checked."""
        found: list[Infection] = []
        found += self.scan_unsupported()
        found += self.scan_cycles()
        found += self.scan_contradictions()
        return found

    def refresh_severities(self) -> int:
        """Recompute severity and spread for every open infection.

        An outbreak detected at hop 1 gets worse as other agents derive from it.
        Because severity is *defined* as graph reach, it has to be recomputed as
        the graph changes rather than stored once at detection time.
        """
        rows = self.store.rows(
            """
            MATCH (i:Infection)-[:TARGETS]->(c:Claim)
            WHERE i.status IN ['active', 'contained']
            RETURN i.id, c.id
            """
        )
        for infection_id, root_claim_id in rows:
            infected, exposed = self.cascade_reach(root_claim_id)
            self.writer.set_infection_severity(
                infection_id, severity_from_reach(infected, exposed)
            )
            for downstream_id in self.downstream_claims(root_claim_id):
                self.writer.link_infection_spread(infection_id, downstream_id)
        return len(rows)

    def sweep(self) -> list[Infection]:
        """Full surveillance pass: detect new infections, then refresh open ones."""
        found = self.scan_all()
        self.refresh_severities()
        return found

    # -------------------------------------------------------------- recall

    def recall(self, text: str, embedding: list[float] | None = None) -> RecallResult:
        """Has the swarm been infected by this claim before?

        Three paths, cheapest first:
          1. exact fingerprint match (indexed, effectively instant)
          2. vector index over embeddings (semantic, needs an embedding)
          3. graph-sourced candidates scored by token overlap in Python
        """
        from .models import fingerprint as fp

        # --- 1. exact ------------------------------------------------------
        rows = self.store.rows(Q6_IMMUNITY_RECALL, {"fingerprint": fp(text)})
        if rows:
            r = rows[0]
            return RecallResult(
                hit=True, method="fingerprint", past_claim_id=r[0], past_text=r[1],
                past_status=r[2], infection_id=r[3], infection_type=r[4],
                first_seen=r[5], contained_at=r[6],
            )

        # --- 2. vector -----------------------------------------------------
        if embedding:
            try:
                rows = self.store.rows(
                    Q7_VECTOR_RECALL, {"query_embedding": list(embedding), "k": 5}
                )
                if rows:
                    r = rows[0]
                    distance = float(r[3])
                    if distance <= VECTOR_MATCH_MAX_DISTANCE:
                        return RecallResult(
                            hit=True, method="vector", past_claim_id=r[0], past_text=r[1],
                            past_status=r[2], infection_id=r[5] if len(r) > 5 else None,
                            infection_type=r[4], score=distance,
                        )
            except Exception:  # noqa: BLE001 - vector support is optional
                pass

        # --- 3. token-overlap fallback -------------------------------------
        # The candidate set still comes from the graph; Python only ranks it.
        best: tuple[float, list] | None = None
        for row in self.store.rows(Q7B_RECALL_CANDIDATES):
            score = similarity(text, row[1] or "")
            if best is None or score > best[0]:
                best = (score, row)
        if best and best[0] >= SIMILARITY_MATCH_MIN:
            score, row = best
            return RecallResult(
                hit=True, method="similarity", past_claim_id=row[0], past_text=row[1],
                past_status=row[3], infection_id=row[4], infection_type=row[5],
                first_seen=row[6], score=score,
            )

        return RecallResult(hit=False)

    def contain_repeat(self, new_claim_id: str, recall: RecallResult) -> Infection | None:
        """Quarantine a repeat offender and record that immunity worked.

        This is the payoff beat: no cascade re-detection is needed because the
        swarm already knows this claim.
        """
        if not recall.hit or not recall.past_claim_id:
            return None

        self.writer.link_repeat(new_claim_id, recall.past_claim_id, recall.score or 1.0)
        self.writer.set_status(new_claim_id, "quarantined")

        infection = Infection(
            id=_new_id("inf"),
            type=INFECTION_REPEAT,
            status=INFECTION_CONTAINED,
            severity=1.0,
            detected_at=utcnow(),
        )
        self.writer.record_infection(infection)
        self.writer.link_infection_target(infection.id, new_claim_id)

        # The earlier antibody demonstrably worked: the swarm recognised the
        # claim without help. That is the signal Q8 measures.
        self.writer.mark_antibodies_effective_for(recall.past_claim_id)

        # The recall is itself an immune response, so it is recorded as one —
        # otherwise Q8 would silently omit the most interesting outbreak type.
        antibody = Antibody(
            id=_new_id("ab"),
            correction_text=(
                f"Immunity recall: this restates {recall.past_text!r}, which was "
                f"quarantined after {'an' if (recall.infection_type or '')[:1].lower() in 'aeiou' else 'a'} "
                f"{recall.infection_type} infection first seen {recall.first_seen}."
            ),
            strategy="immunity_recall",
            outcome="effective",
            created_at=utcnow(),
        )
        self.writer.record_antibody(antibody, infection.id)
        return infection
    # -------------------------------------------------------------- trust

    def trust_table(self) -> list[dict]:
        """Q5: trust derived from the graph, never mutated in place."""
        rows = self.store.rows(
            """
            MATCH (a:Agent)
            OPTIONAL MATCH (a)-[:ASSERTS]->(c:Claim)<-[:TARGETS]-(i1:Infection)
              WHERE i1.status IN ['active', 'contained']
            OPTIONAL MATCH (a)-[:ASSERTS]->(d:Claim)-[:DERIVED_FROM*1..5]->(:Claim)<-[:TARGETS]-(i2:Infection)
              WHERE i2.status IN ['active', 'contained']
            WITH a, collect(DISTINCT i1) + collect(DISTINCT i2) AS raw
            WITH a, [x IN raw WHERE x IS NOT NULL] AS infs
            RETURN a.name, a.base_trust, size(infs) AS active_infections,
                   a.base_trust - ($decay * size(infs)) AS effective_trust
            ORDER BY effective_trust ASC
            """,
            {"decay": TRUST_DECAY_PER_INFECTION},
        )
        # NOTE: the Q5 copy in queries.py hardcodes the decay; this variant is
        # parameterised so the constant lives in exactly one place.
        return [
            {
                "agent": r[0],
                "base_trust": float(r[1] or 0),
                "active_infections": int(r[2] or 0),
                "effective_trust": float(r[3] or 0),
            }
            for r in rows
        ]

    def active_infections(self) -> list[dict]:
        rows = self.store.rows(
            """
            MATCH (i:Infection)-[:TARGETS]->(c:Claim)
            WHERE i.status IN ['active', 'contained']
            OPTIONAL MATCH (i)-[:SPREAD_TO]->(spread:Claim)
            RETURN i.id, i.type, i.severity, i.status, c.id, c.text,
                   count(DISTINCT spread), i.detected_at
            ORDER BY i.severity DESC, i.detected_at DESC
            """
        )
        return [
            {
                "infection_id": r[0], "type": r[1], "severity": float(r[2] or 0),
                "status": r[3], "root_claim_id": r[4], "root_claim": r[5],
                "spread_count": int(r[6] or 0), "detected_at": r[7],
            }
            for r in rows
        ]

    def antibody_effectiveness(self) -> list[dict]:
        """Q8: did the immune response actually work?"""
        rows = self.store.rows(
            """
            MATCH (ab:Antibody)-[:NEUTRALIZES]->(i:Infection)
            RETURN i.type,
                   count(i) AS outbreaks,
                   sum(CASE WHEN ab.outcome = 'effective' THEN 1 ELSE 0 END) AS contained,
                   sum(CASE WHEN ab.outcome = 'ignored' THEN 1 ELSE 0 END) AS recurred
            ORDER BY outbreaks DESC
            """
        )
        return [
            {"infection_type": r[0], "outbreaks": int(r[1] or 0),
             "contained": int(r[2] or 0), "recurred": int(r[3] or 0)}
            for r in rows
        ]
