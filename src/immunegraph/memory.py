"""Graph write helpers.

Keeps every Cypher INSERT in one place so the read-only detection queries in
queries.py stay clean and reviewable, and so the schema in docs/GRAPH_MODEL.md
has exactly one code counterpart.

All writes are parameterised. All temporal properties are ISO-8601 UTC strings.
"""

from __future__ import annotations

import functools

from typing import Any

from .models import Agent, Antibody, Claim, Infection, Source, utcnow
from .schema import CLAIM_SUSPECT, KERNEL_ACTIVE, KERNEL_SUPERSEDED, TRUST_PRIMARY


def logs(label: str):
    """Tag the writes a MemoryWriter method performs with a semantic label.

    The label is for humans reading the log; replay uses the statement itself,
    so a wrong label can never corrupt a rebuild.
    """

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(self, *args, **kwargs):
            previous = self._current_label
            self._current_label = label
            try:
                return fn(self, *args, **kwargs)
            finally:
                self._current_label = previous

        return wrapper

    return decorator


class MemoryWriter:
    """Writes the episodic and semantic layers into FalkorDB."""

    def __init__(self, store) -> None:
        self.store = store
        self._current_label: str | None = None

    def _write(self, cypher: str, params: dict | None = None):
        """Route a mutation through the store's write-ahead log."""
        return self.store.write(self._current_label or "Write", cypher, params or {})

    # ------------------------------------------------------------ sessions

    @logs("SessionOpened")
    def open_session(self, session_id: str, topic: str, tenant_id: str = "default") -> None:
        self._write(
            """
            MERGE (s:Session {id: $id})
            SET s.topic = $topic, s.tenant_id = $tenant_id,
                s.started_at = coalesce(s.started_at, $now), s.status = 'open'
            """,
            {"id": session_id, "topic": topic, "tenant_id": tenant_id, "now": utcnow()},
        )

    @logs("SessionClosed")
    def close_session(self, session_id: str) -> None:
        self._write(
            "MATCH (s:Session {id: $id}) SET s.status = 'closed', s.ended_at = $now",
            {"id": session_id, "now": utcnow()},
        )

    _COUNTER_NAMES = frozenset({"seq", "claim"})

    @logs("CounterAllocated")
    def allocate_counter(self, session_id: str, name: str) -> int:
        """Atomically allocate the next value of a per-session counter.

        Deliberately implemented as a *write* (``SET``) so FalkorDB serialises
        it. The obvious implementation, read ``max(seq)`` and add one, is a
        read-only query, and concurrent readers are free to observe the same
        value, which produces duplicate sequence numbers and colliding ids.

        ``name`` is validated against an allow-list because it is interpolated
        into the query text.
        """
        if name not in self._COUNTER_NAMES:
            raise ValueError(f"unknown counter {name!r}")
        prop = f"{name}_counter"
        row = self.store.one(
            f"""
            MATCH (s:Session {{id: $sid}})
            WITH s, coalesce(s.{prop}, 0) AS current
            SET s.{prop} = current + 1
            RETURN s.{prop}
            """,
            {"sid": session_id},
        )
        if row is None:
            raise RuntimeError(f"session {session_id!r} does not exist")
        return int(row[0])

    @logs("MessageRecorded")
    def record_message(
        self,
        session_id: str,
        agent_id: str,
        text: str,
        role: str = "agent",
    ) -> tuple[str, int]:
        """Append an immutable utterance. Returns (message_id, seq).

        The id is derived from the atomically allocated sequence number, so
        concurrent writers cannot collide.
        """
        seq = self.allocate_counter(session_id, "seq")
        message_id = f"msg_{session_id}_{seq:04d}"
        self._write(
            """
            MATCH (s:Session {id: $session_id})
            CREATE (m:Message {id: $id, session_id: $session_id, seq: $seq,
                               text: $text, ts: $ts, role: $role})
            CREATE (s)-[:CONTAINS]->(m)
            """,
            {
                "id": message_id,
                "session_id": session_id,
                "seq": seq,
                "text": text,
                "ts": utcnow(),
                "role": role,
            },
        )
        self._write(
            """
            MATCH (a:Agent {id: $agent_id}), (m:Message {id: $message_id})
            CREATE (a)-[:SPOKE {ts: $ts}]->(m)
            """,
            {"agent_id": agent_id, "message_id": message_id, "ts": utcnow()},
        )
        return message_id, seq

    # -------------------------------------------------------------- agents

    @logs("AgentRegistered")
    def upsert_agent(self, agent: Agent) -> None:
        self._write(
            """
            MERGE (a:Agent {id: $id})
            SET a.name = $name, a.role = $role,
                a.base_trust = coalesce(a.base_trust, $base_trust),
                a.tenant_id = $tenant_id
            """,
            agent.to_properties(),
        )

    @logs("TrustLinked")
    def link_trust(self, truster_id: str, trustee_id: str, weight: float = 1.0) -> None:
        self._write(
            """
            MATCH (t:Agent {id: $truster}), (u:Agent {id: $trustee})
            MERGE (t)-[r:TRUSTS]->(u) SET r.weight = $weight
            """,
            {"truster": truster_id, "trustee": trustee_id, "weight": weight},
        )

    # -------------------------------------------------------- semantic layer

    @logs("ClaimAsserted")
    def record_claim(
        self,
        claim: Claim,
        agent_id: str,
        session_id: str | None = None,
        message_id: str | None = None,
    ) -> str:
        """Create the claim and link the asserting agent to it."""
        self._write(
            """
            CREATE (c:Claim {id: $id, text: $text, fingerprint: $fingerprint,
                             status: $status, verification: $verification,
                             confidence: $confidence, created_at: $created_at})
            """,
            claim.to_properties(),
        )
        self._write(
            """
            MATCH (a:Agent {id: $agent_id}), (c:Claim {id: $claim_id})
            CREATE (a)-[:ASSERTS {ts: $ts, message_id: $message_id}]->(c)
            """,
            {
                "agent_id": agent_id,
                "claim_id": claim.id,
                "ts": utcnow(),
                "message_id": message_id or "",
            },
        )
        if session_id:
            self._write(
                "MATCH (s:Session {id: $sid}), (c:Claim {id: $cid}) CREATE (s)-[:CONTAINS]->(c)",
                {"sid": session_id, "cid": claim.id},
            )
        if message_id:
            self._write(
                "MATCH (m:Message {id: $mid}), (c:Claim {id: $cid}) CREATE (m)-[:MENTIONS]->(c)",
                {"mid": message_id, "cid": claim.id},
            )
        return claim.id

    @logs("SourceUpserted")
    def upsert_source(self, source: Source) -> None:
        self._write(
            """
            MERGE (s:Source {id: $id})
            SET s.name = $name, s.url = $url, s.trust_level = $trust_level
            """,
            source.to_properties(),
        )

    @logs("StatementRefuted")
    def record_refuted_statement(self, statement: "RefutedStatement") -> str:
        """Store a known-false statement. Negative memory, and logged like any write."""
        self._write(
            """
            MERGE (r:RefutedStatement {id: $id})
            SET r.text = $text, r.fact_id = $fact_id,
                r.reason = $reason, r.created_at = $created_at
            """,
            statement.to_properties(),
        )
        return statement.id

    @logs("SourceCited")
    def link_source(self, claim_id: str, source_id: str, quote: str = "") -> None:
        """Attach a citation, and promote the claim's verification state."""
        self._write(
            """
            MATCH (c:Claim {id: $claim_id}), (s:Source {id: $source_id})
            MERGE (c)-[r:SUPPORTED_BY]->(s) SET r.quote = $quote
            """,
            {"claim_id": claim_id, "source_id": source_id, "quote": quote},
        )
        self._write(
            "MATCH (c:Claim {id: $claim_id}) SET c.verification = 'sourced'",
            {"claim_id": claim_id},
        )

    @logs("ClaimDerived")
    def link_derivation(self, child_id: str, parent_id: str) -> None:
        """child -> parent. Cascades therefore walk back to the root."""
        self._write(
            """
            MATCH (child:Claim {id: $child_id}), (parent:Claim {id: $parent_id})
            MERGE (child)-[r:DERIVED_FROM]->(parent) SET r.ts = $ts
            """,
            {"child_id": child_id, "parent_id": parent_id, "ts": utcnow()},
        )

    @logs("ContradictionRecorded")
    def link_contradiction(
        self,
        a_id: str,
        b_id: str,
        reason: str = "",
        update_verification: bool = True,
    ) -> None:
        """Record that two claims conflict.

        ``update_verification`` is False when the caller is an antibody: the
        injected correction is itself sourced, so it must not be marked
        'contradicted' just because it conflicts with the claim it refutes.
        """
        self._write(
            """
            MATCH (a:Claim {id: $a_id}), (b:Claim {id: $b_id})
            MERGE (a)-[r:CONTRADICTS]->(b)
            SET r.detected_at = $ts, r.reason = $reason
            """,
            {"a_id": a_id, "b_id": b_id, "ts": utcnow(), "reason": reason},
        )
        if update_verification:
            for cid in (a_id, b_id):
                self.set_verification(cid, "contradicted")

    @logs("ClaimVerificationSet")
    def set_verification(self, claim_id: str, verification: str) -> None:
        self._write(
            "MATCH (c:Claim {id: $cid}) SET c.verification = $verification",
            {"cid": claim_id, "verification": verification},
        )

    @logs("ClaimRepeated")
    def link_repeat(self, new_id: str, old_id: str, similarity: float) -> None:
        self._write(
            """
            MATCH (n:Claim {id: $new_id}), (o:Claim {id: $old_id})
            MERGE (n)-[r:REPEATS]->(o) SET r.similarity = $similarity
            """,
            {"new_id": new_id, "old_id": old_id, "similarity": similarity},
        )

    @logs("ClaimStatusChanged")
    def set_status(self, claim_id: str, status: str) -> None:
        self._write(
            "MATCH (c:Claim {id: $cid}) SET c.status = $status",
            {"cid": claim_id, "status": status},
        )

    @logs("ClaimEmbeddingSet")
    def set_embedding(self, claim_id: str, vector: list[float]) -> None:
        """Store an embedding as a true vector so the HNSW index can use it."""
        self._write(
            "MATCH (c:Claim {id: $cid}) SET c.embedding = vecf32($vec)",
            {"cid": claim_id, "vec": [float(x) for x in vector]},
        )

    # ------------------------------------------------------- immune response

    @logs("InfectionDetected")
    def record_infection(self, infection: Infection) -> str:
        self._write(
            """
            CREATE (i:Infection {id: $id, type: $type, status: $status,
                                 severity: $severity, detected_at: $detected_at,
                                 resolved_at: $resolved_at})
            """,
            infection.to_properties(),
        )
        return infection.id

    @logs("InfectionTargeted")
    def link_infection_target(self, infection_id: str, claim_id: str) -> None:
        self._write(
            """
            MATCH (i:Infection {id: $iid}), (c:Claim {id: $cid})
            MERGE (i)-[:TARGETS]->(c)
            """,
            {"iid": infection_id, "cid": claim_id},
        )

    @logs("InfectionSpread")
    def link_infection_spread(self, infection_id: str, claim_id: str) -> None:
        self._write(
            """
            MATCH (i:Infection {id: $iid}), (c:Claim {id: $cid})
            MERGE (i)-[:SPREAD_TO]->(c)
            """,
            {"iid": infection_id, "cid": claim_id},
        )

    @logs("InfectionStatusChanged")
    def set_infection_status(self, infection_id: str, status: str, severity: float | None = None) -> None:
        if severity is None:
            self._write(
                "MATCH (i:Infection {id: $iid}) SET i.status = $status, "
                "i.resolved_at = CASE WHEN $status = 'resolved' THEN $now ELSE i.resolved_at END",
                {"iid": infection_id, "status": status, "now": utcnow()},
            )
        else:
            self._write(
                "MATCH (i:Infection {id: $iid}) SET i.status = $status, i.severity = $severity, "
                "i.resolved_at = CASE WHEN $status = 'resolved' THEN $now ELSE i.resolved_at END",
                {"iid": infection_id, "status": status, "severity": severity, "now": utcnow()},
            )

    @logs("AntibodyInjected")
    def record_antibody(self, antibody: Antibody, infection_id: str) -> str:
        self._write(
            """
            CREATE (ab:Antibody {id: $id, correction_text: $correction_text,
                                 strategy: $strategy, outcome: $outcome,
                                 created_at: $created_at})
            """,
            antibody.to_properties(),
        )
        self._write(
            """
            MATCH (ab:Antibody {id: $abid}), (i:Infection {id: $iid})
            MERGE (ab)-[:NEUTRALIZES]->(i)
            """,
            {"abid": antibody.id, "iid": infection_id},
        )
        return antibody.id

    @logs("AntibodyCorrectionLinked")
    def link_antibody_injection(self, antibody_id: str, claim_id: str) -> None:
        self._write(
            """
            MATCH (ab:Antibody {id: $abid}), (c:Claim {id: $cid})
            MERGE (ab)-[:INJECTS]->(c)
            """,
            {"abid": antibody_id, "cid": claim_id},
        )

    @logs("AntibodyOutcomeSet")
    def set_antibody_outcome(self, antibody_id: str, outcome: str) -> None:
        self._write(
            "MATCH (ab:Antibody {id: $abid}) SET ab.outcome = $outcome",
            {"abid": antibody_id, "outcome": outcome},
        )

    # --------------------------------------------------------- kernel of truth

    @logs("KernelFactRegistered")
    def record_kernel_fact(
        self,
        fact_id: str,
        text: str,
        trust_level: str = TRUST_PRIMARY,
        version: int = 1,
        status: str = KERNEL_ACTIVE,
        created_at: str | None = None,
    ) -> str:
        """A canonical, source-backed statement the swarm now holds as true."""
        self._write(
            """
            MERGE (k:KernelFact {id: $id})
            SET k.text = $text, k.trust_level = $trust_level,
                k.version = $version, k.status = $status,
                k.created_at = coalesce(k.created_at, $created_at)
            """,
            {
                "id": fact_id,
                "text": text,
                "trust_level": trust_level,
                "version": version,
                "status": status,
                "created_at": created_at or utcnow(),
            },
        )
        return fact_id

    @logs("KernelFactSourced")
    def link_fact_source(self, fact_id: str, source_id: str) -> None:
        self._write(
            """
            MATCH (k:KernelFact {id: $fid}), (s:Source {id: $sid})
            MERGE (k)-[:DERIVED_FROM]->(s)
            """,
            {"fid": fact_id, "sid": source_id},
        )

    @logs("KernelFactEstablished")
    def link_antibody_establishes(self, antibody_id: str, fact_id: str) -> None:
        """The antibody *teaches*, not just punishes."""
        self._write(
            """
            MATCH (ab:Antibody {id: $abid}), (k:KernelFact {id: $fid})
            MERGE (ab)-[:ESTABLISHES]->(k)
            """,
            {"abid": antibody_id, "fid": fact_id},
        )

    @logs("ClaimConflictsWithKernel")
    def link_claim_conflicts_with(self, claim_id: str, fact_id: str, score: float) -> None:
        self._write(
            """
            MATCH (c:Claim {id: $cid}), (k:KernelFact {id: $fid})
            MERGE (c)-[r:CONFLICTS_WITH]->(k)
            SET r.score = $score, r.detected_at = $ts
            """,
            {"cid": claim_id, "fid": fact_id, "score": score, "ts": utcnow()},
        )

    @logs("ClaimAlignedWithKernel")
    def link_claim_aligned_with(self, claim_id: str, fact_id: str, score: float) -> None:
        self._write(
            """
            MATCH (c:Claim {id: $cid}), (k:KernelFact {id: $fid})
            MERGE (c)-[r:ALIGNED_WITH]->(k)
            SET r.score = $score, r.detected_at = $ts
            """,
            {"cid": claim_id, "fid": fact_id, "score": score, "ts": utcnow()},
        )

    @logs("ClaimVerdictSet")
    def set_claim_verdict(self, claim_id: str, verdict: str) -> None:
        self._write(
            "MATCH (c:Claim {id: $cid}) SET c.verdict = $verdict",
            {"cid": claim_id, "verdict": verdict},
        )

    @logs("ClaimSuspectIfActive")
    def mark_claim_suspect_if_active(self, claim_id: str) -> None:
        """Downgrade a claim to 'suspect' without clobbering a stronger state.

        Cascade detection may already have quarantined this claim, and
        'quarantined' is stronger than 'suspect'. Sanitisation must never
        weaken a verdict that detection already reached.
        """
        self._write(
            """
            MATCH (c:Claim {id: $cid})
            WHERE c.status = 'active'
            SET c.status = $suspect
            """,
            {"cid": claim_id, "suspect": CLAIM_SUSPECT},
        )

    @logs("KernelFactSuperseded")
    def supersede_kernel_fact(self, old_id: str, new_id: str) -> None:
        """Retire a fact in favour of a newer one, keeping the history."""
        self._write(
            """
            MATCH (old:KernelFact {id: $old_id}), (new:KernelFact {id: $new_id})
            SET old.status = $superseded, old.superseded_by = $new_id
            MERGE (new)-[:SUPERSEDES]->(old)
            """,
            {"old_id": old_id, "new_id": new_id, "superseded": KERNEL_SUPERSEDED},
        )

    @logs("InfectionSeveritySet")
    def set_infection_severity(self, infection_id: str, severity: float) -> None:
        """Severity is recomputed as a cascade grows, so it has to be logged too."""
        self._write(
            "MATCH (i:Infection {id: $iid}) SET i.severity = $severity",
            {"iid": infection_id, "severity": severity},
        )

    @logs("AntibodyMarkedEffective")
    def mark_antibodies_effective_for(self, claim_id: str) -> None:
        """Credit the antibody that handled a claim the swarm just recognised."""
        self._write(
            """
            MATCH (ab:Antibody)-[:NEUTRALIZES]->(i:Infection)-[:TARGETS]->(:Claim {id: $past})
            WHERE ab.outcome = 'unknown'
            SET ab.outcome = 'effective'
            """,
            {"past": claim_id},
        )

    @logs("CascadeContained")
    def contain_cascade(self, root_claim_id: str) -> list[str]:
        """Downgrade claims whose only support descends from a quarantined root.

        Issued through the write path deliberately. The natural implementation
        returns ids, so it reads as a query and gets written with ``rows()`` -
        which is not logged, so a rebuild would silently lose the cascade
        containment. That is precisely what the graph fingerprint caught.
        """
        result = self._write(
            """
            MATCH (down:Claim)-[:DERIVED_FROM*1..5]->(root:Claim {id: $root})
            WHERE down.status = 'active'
            OPTIONAL MATCH (down)-[:SUPPORTED_BY]->(s:Source)
              WHERE s.trust_level IN ['primary', 'reputable']
            WITH down, count(s) AS independent
            WHERE independent = 0
            SET down.status = $suspect
            RETURN down.id
            """,
            {"root": root_claim_id, "suspect": CLAIM_SUSPECT},
        )
        return [row[0] for row in result.result_set]

    # ------------------------------------------------------------- utilities

    def existing_message_ids(self, session_id: str) -> list[str]:
        rows = self.store.rows(
            "MATCH (m:Message {session_id: $sid}) RETURN m.id ORDER BY m.seq",
            {"sid": session_id},
        )
        return [r[0] for r in rows]

    def all_rows(self, cypher: str, params: dict[str, Any] | None = None):
        return self.store.rows(cypher, params)
