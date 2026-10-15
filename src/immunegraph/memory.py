"""Graph write helpers.

Keeps every Cypher INSERT in one place so the read-only detection queries in
queries.py stay clean and reviewable, and so the schema in docs/GRAPH_MODEL.md
has exactly one code counterpart.

All writes are parameterised. All temporal properties are ISO-8601 UTC strings.
"""

from __future__ import annotations

from typing import Any

from .models import Agent, Antibody, Claim, Infection, Source, utcnow


class MemoryWriter:
    """Writes the episodic and semantic layers into FalkorDB."""

    def __init__(self, store) -> None:
        self.store = store

    # ------------------------------------------------------------ sessions

    def open_session(self, session_id: str, topic: str, tenant_id: str = "default") -> None:
        self.store.query(
            """
            MERGE (s:Session {id: $id})
            SET s.topic = $topic, s.tenant_id = $tenant_id,
                s.started_at = coalesce(s.started_at, $now), s.status = 'open'
            """,
            {"id": session_id, "topic": topic, "tenant_id": tenant_id, "now": utcnow()},
        )

    def close_session(self, session_id: str) -> None:
        self.store.query(
            "MATCH (s:Session {id: $id}) SET s.status = 'closed', s.ended_at = $now",
            {"id": session_id, "now": utcnow()},
        )

    _COUNTER_NAMES = frozenset({"seq", "claim"})

    def allocate_counter(self, session_id: str, name: str) -> int:
        """Atomically allocate the next value of a per-session counter.

        Deliberately implemented as a *write* (``SET``) so FalkorDB serialises
        it. The obvious implementation — read ``max(seq)`` and add one — is a
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
        self.store.query(
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
        self.store.query(
            """
            MATCH (a:Agent {id: $agent_id}), (m:Message {id: $message_id})
            CREATE (a)-[:SPOKE {ts: $ts}]->(m)
            """,
            {"agent_id": agent_id, "message_id": message_id, "ts": utcnow()},
        )
        return message_id, seq

    # -------------------------------------------------------------- agents

    def upsert_agent(self, agent: Agent) -> None:
        self.store.query(
            """
            MERGE (a:Agent {id: $id})
            SET a.name = $name, a.role = $role,
                a.base_trust = coalesce(a.base_trust, $base_trust),
                a.tenant_id = $tenant_id
            """,
            agent.to_properties(),
        )

    def link_trust(self, truster_id: str, trustee_id: str, weight: float = 1.0) -> None:
        self.store.query(
            """
            MATCH (t:Agent {id: $truster}), (u:Agent {id: $trustee})
            MERGE (t)-[r:TRUSTS]->(u) SET r.weight = $weight
            """,
            {"truster": truster_id, "trustee": trustee_id, "weight": weight},
        )

    # -------------------------------------------------------- semantic layer

    def record_claim(
        self,
        claim: Claim,
        agent_id: str,
        session_id: str | None = None,
        message_id: str | None = None,
    ) -> str:
        """Create the claim and link the asserting agent to it."""
        self.store.query(
            """
            CREATE (c:Claim {id: $id, text: $text, fingerprint: $fingerprint,
                             status: $status, verification: $verification,
                             confidence: $confidence, created_at: $created_at})
            """,
            claim.to_properties(),
        )
        self.store.query(
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
            self.store.query(
                "MATCH (s:Session {id: $sid}), (c:Claim {id: $cid}) CREATE (s)-[:CONTAINS]->(c)",
                {"sid": session_id, "cid": claim.id},
            )
        if message_id:
            self.store.query(
                "MATCH (m:Message {id: $mid}), (c:Claim {id: $cid}) CREATE (m)-[:MENTIONS]->(c)",
                {"mid": message_id, "cid": claim.id},
            )
        return claim.id

    def upsert_source(self, source: Source) -> None:
        self.store.query(
            """
            MERGE (s:Source {id: $id})
            SET s.name = $name, s.url = $url, s.trust_level = $trust_level
            """,
            source.to_properties(),
        )

    def link_source(self, claim_id: str, source_id: str, quote: str = "") -> None:
        """Attach a citation, and promote the claim's verification state."""
        self.store.query(
            """
            MATCH (c:Claim {id: $claim_id}), (s:Source {id: $source_id})
            MERGE (c)-[r:SUPPORTED_BY]->(s) SET r.quote = $quote
            """,
            {"claim_id": claim_id, "source_id": source_id, "quote": quote},
        )
        self.store.query(
            "MATCH (c:Claim {id: $claim_id}) SET c.verification = 'sourced'",
            {"claim_id": claim_id},
        )

    def link_derivation(self, child_id: str, parent_id: str) -> None:
        """child -> parent. Cascades therefore walk back to the root."""
        self.store.query(
            """
            MATCH (child:Claim {id: $child_id}), (parent:Claim {id: $parent_id})
            MERGE (child)-[r:DERIVED_FROM]->(parent) SET r.ts = $ts
            """,
            {"child_id": child_id, "parent_id": parent_id, "ts": utcnow()},
        )

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
        self.store.query(
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

    def set_verification(self, claim_id: str, verification: str) -> None:
        self.store.query(
            "MATCH (c:Claim {id: $cid}) SET c.verification = $verification",
            {"cid": claim_id, "verification": verification},
        )

    def link_repeat(self, new_id: str, old_id: str, similarity: float) -> None:
        self.store.query(
            """
            MATCH (n:Claim {id: $new_id}), (o:Claim {id: $old_id})
            MERGE (n)-[r:REPEATS]->(o) SET r.similarity = $similarity
            """,
            {"new_id": new_id, "old_id": old_id, "similarity": similarity},
        )

    def set_status(self, claim_id: str, status: str) -> None:
        self.store.query(
            "MATCH (c:Claim {id: $cid}) SET c.status = $status",
            {"cid": claim_id, "status": status},
        )

    def set_embedding(self, claim_id: str, vector: list[float]) -> None:
        """Store an embedding as a true vector so the HNSW index can use it."""
        self.store.query(
            "MATCH (c:Claim {id: $cid}) SET c.embedding = vecf32($vec)",
            {"cid": claim_id, "vec": [float(x) for x in vector]},
        )

    # ------------------------------------------------------- immune response

    def record_infection(self, infection: Infection) -> str:
        self.store.query(
            """
            CREATE (i:Infection {id: $id, type: $type, status: $status,
                                 severity: $severity, detected_at: $detected_at,
                                 resolved_at: $resolved_at})
            """,
            infection.to_properties(),
        )
        return infection.id

    def link_infection_target(self, infection_id: str, claim_id: str) -> None:
        self.store.query(
            """
            MATCH (i:Infection {id: $iid}), (c:Claim {id: $cid})
            MERGE (i)-[:TARGETS]->(c)
            """,
            {"iid": infection_id, "cid": claim_id},
        )

    def link_infection_spread(self, infection_id: str, claim_id: str) -> None:
        self.store.query(
            """
            MATCH (i:Infection {id: $iid}), (c:Claim {id: $cid})
            MERGE (i)-[:SPREAD_TO]->(c)
            """,
            {"iid": infection_id, "cid": claim_id},
        )

    def set_infection_status(self, infection_id: str, status: str, severity: float | None = None) -> None:
        if severity is None:
            self.store.query(
                "MATCH (i:Infection {id: $iid}) SET i.status = $status, "
                "i.resolved_at = CASE WHEN $status = 'resolved' THEN $now ELSE i.resolved_at END",
                {"iid": infection_id, "status": status, "now": utcnow()},
            )
        else:
            self.store.query(
                "MATCH (i:Infection {id: $iid}) SET i.status = $status, i.severity = $severity, "
                "i.resolved_at = CASE WHEN $status = 'resolved' THEN $now ELSE i.resolved_at END",
                {"iid": infection_id, "status": status, "severity": severity, "now": utcnow()},
            )

    def record_antibody(self, antibody: Antibody, infection_id: str) -> str:
        self.store.query(
            """
            CREATE (ab:Antibody {id: $id, correction_text: $correction_text,
                                 strategy: $strategy, outcome: $outcome,
                                 created_at: $created_at})
            """,
            antibody.to_properties(),
        )
        self.store.query(
            """
            MATCH (ab:Antibody {id: $abid}), (i:Infection {id: $iid})
            MERGE (ab)-[:NEUTRALIZES]->(i)
            """,
            {"abid": antibody.id, "iid": infection_id},
        )
        return antibody.id

    def link_antibody_injection(self, antibody_id: str, claim_id: str) -> None:
        self.store.query(
            """
            MATCH (ab:Antibody {id: $abid}), (c:Claim {id: $cid})
            MERGE (ab)-[:INJECTS]->(c)
            """,
            {"abid": antibody_id, "cid": claim_id},
        )

    def set_antibody_outcome(self, antibody_id: str, outcome: str) -> None:
        self.store.query(
            "MATCH (ab:Antibody {id: $abid}) SET ab.outcome = $outcome",
            {"abid": antibody_id, "outcome": outcome},
        )

    # ------------------------------------------------------------- utilities

    def existing_message_ids(self, session_id: str) -> list[str]:
        rows = self.store.rows(
            "MATCH (m:Message {session_id: $sid}) RETURN m.id ORDER BY m.seq",
            {"sid": session_id},
        )
        return [r[0] for r in rows]

    def all_rows(self, cypher: str, params: dict[str, Any] | None = None):
        return self.store.rows(cypher, params)
