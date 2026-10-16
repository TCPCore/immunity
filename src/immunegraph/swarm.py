"""The swarm orchestrator.

One place that turns an agent utterance into graph facts, runs the immune
surveillance pass, and dispatches antibodies. Both the Streamlit app and the
headless demo drive this same object, so what you see in the UI is exactly
what the demo video runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .agents import DEFAULT_SWARM, MARS_TOPIC, Persona
from .antibody import AntibodyAgent, AntibodyResult
from .extractor import ExtractedSource, get_extractor
from .immune import ImmuneScanner, RecallResult, TRUST_DECAY_PER_INFECTION
from .kernel import Kernel
from .memory import MemoryWriter
from .models import Agent, Claim, Source
from .sanitizer import Sanitizer
from .schema import CLAIM_ACTIVE


@dataclass
class TurnResult:
    """What one utterance did to the graph."""

    agent: str
    text: str
    message_id: str
    method: str
    claim_ids: list[str] = field(default_factory=list)
    claim_texts: list[str] = field(default_factory=list)
    source_names: list[str] = field(default_factory=list)
    derivations: list[tuple[str, str]] = field(default_factory=list)
    contradictions: list[tuple[str, str, str]] = field(default_factory=list)
    new_infection_ids: list[str] = field(default_factory=list)
    verdicts: list[dict] = field(default_factory=list)


class Swarm:
    """Shared-state multi-agent conversation backed by FalkorDB."""

    def __init__(self, store, extractor=None, tenant_id: str = "default") -> None:
        self.store = store
        self.writer = MemoryWriter(store)
        self.extractor = extractor or get_extractor()
        self.scanner = ImmuneScanner(store, self.writer)
        # The kernel is shared by the antibody (which writes to it) and the
        # sanitiser (which reads it), so both halves of the immune response
        # agree on what the swarm currently holds as true.
        self.kernel = Kernel(store, self.writer)
        self.antibody = AntibodyAgent(store, self.writer, kernel=self.kernel)
        self.sanitizer = Sanitizer(store, self.writer, kernel=self.kernel)
        self.tenant_id = tenant_id
        self.session_id: str | None = None

    # --------------------------------------------------------------- setup

    def start(self, topic: str = MARS_TOPIC, session_id: str = "session_mars") -> str:
        self.session_id = session_id
        self.writer.open_session(session_id, topic, self.tenant_id)
        for persona in DEFAULT_SWARM:
            self.writer.upsert_agent(
                Agent(
                    id=persona.id,
                    name=persona.name,
                    role=persona.role,
                    base_trust=persona.base_trust,
                    tenant_id=self.tenant_id,
                )
            )
        self.antibody.ensure_system_agent()
        return session_id

    def _require_session(self) -> str:
        if not self.session_id:
            raise RuntimeError("call Swarm.start() before speaking")
        return self.session_id

    def _new_claim_id(self) -> str:
        """Claim ids come from an atomic per-session counter, not a Python one.

        A module-level counter is not safe when several agents write
        concurrently, which is exactly the Track 02 scenario.
        """
        n = self.writer.allocate_counter(self._require_session(), "claim")
        return f"cl_{self.session_id}_{n:04d}"

    # ----------------------------------------------------------- graph reads

    def recent_claim_texts(self, limit: int = 8) -> list[str]:
        rows = self.store.rows(
            """
            MATCH (m:Message {session_id: $sid})-[:MENTIONS]->(c:Claim)
            RETURN c.text, m.seq
            ORDER BY m.seq DESC
            LIMIT $limit
            """,
            {"sid": self._require_session(), "limit": limit},
        )
        return [r[0] for r in reversed(rows)]

    def most_recent_claim(self) -> tuple[str, str] | None:
        """The latest still-active claim, ordered by conversation position.

        Ordered by message ``seq`` rather than by timestamp, because several
        claims can share a timestamp at second resolution.
        """
        row = self.store.one(
            """
            MATCH (m:Message {session_id: $sid})-[:MENTIONS]->(c:Claim)
            WHERE c.status = $active
            RETURN c.id, c.text, m.seq
            ORDER BY m.seq DESC
            LIMIT 1
            """,
            {"sid": self._require_session(), "active": CLAIM_ACTIVE},
        )
        return (row[0], row[1]) if row else None

    # ---------------------------------------------------------- the talking

    def say(self, persona: Persona, text: str, sweep: bool = True) -> TurnResult:
        """Record an utterance, extract its claims, and run surveillance.

        ``sweep=False`` records the turn without running the detection pass.
        Used when the caller already knows the claim is a repeat and intends to
        contain it via immunity recall rather than re-diagnose the cascade.
        """
        session_id = self._require_session()
        self.writer.upsert_agent(
            Agent(
                id=persona.id,
                name=persona.name,
                role=persona.role,
                base_trust=persona.base_trust,
                tenant_id=self.tenant_id,
            )
        )

        message_id, _seq = self.writer.record_message(session_id, persona.id, text)

        # Resolve "-1 means the previous claim" BEFORE this turn adds any, so a
        # derivation points at the prior turn rather than at itself.
        previous = self.most_recent_claim()
        prior_texts = self.recent_claim_texts()

        extraction = self.extractor.extract(text, prior_texts)
        method = getattr(self.extractor, "last_method", extraction.method)

        result = TurnResult(
            agent=persona.name, text=text, message_id=message_id, method=method
        )

        source_lookup: dict[str, ExtractedSource] = {
            s.name.lower(): s for s in extraction.sources
        }
        created_ids: list[str] = []

        for extracted in extraction.claims:
            claim_id = self._new_claim_id()
            claim = Claim(id=claim_id, text=extracted.text, confidence=extracted.confidence)
            self.writer.record_claim(claim, persona.id, session_id, message_id)
            created_ids.append(claim_id)
            result.claim_ids.append(claim_id)
            result.claim_texts.append(extracted.text)

            # citations
            for name in extracted.sources:
                meta = source_lookup.get(name.lower()) or ExtractedSource(name=name)
                source = Source(
                    id=f"src_{name.lower().replace(' ', '_')}",
                    name=meta.name or name,
                    url=meta.url,
                    trust_level=meta.trust_level,
                )
                self.writer.upsert_source(source)
                self.writer.link_source(claim_id, source.id)
                result.source_names.append(source.name)

            # derivation edges, child -> parent
            for parent_index in extracted.derived_from:
                if parent_index == -1:
                    parent = previous
                elif 0 <= parent_index < len(created_ids):
                    parent = (created_ids[parent_index], result.claim_texts[parent_index])
                else:
                    parent = None
                if parent and parent[0] != claim_id:
                    self.writer.link_derivation(claim_id, parent[0])
                    result.derivations.append((claim_id, parent[0]))

        # contradiction edges
        for a_index, b_index, reason in extraction.contradictions:
            if not (0 <= a_index < len(created_ids)):
                continue
            if b_index == -1:
                other = previous
            elif 0 <= b_index < len(created_ids):
                other = (created_ids[b_index], result.claim_texts[b_index])
            else:
                other = None
            if not other or other[0] == created_ids[a_index]:
                continue
            # Defensive: a claim cannot contradict the claim it derives from.
            parents = {parent for child, parent in result.derivations if child == created_ids[a_index]}
            if other[0] in parents:
                continue
            self.writer.link_contradiction(created_ids[a_index], other[0], reason)
            result.contradictions.append((created_ids[a_index], other[0], reason))

        # surveillance runs continuously, not only at the end
        if sweep:
            new_infections = self.scanner.sweep()
            result.new_infection_ids = [i.id for i in new_infections]

        # Sanitise every claim this turn produced, whether or not the cascade
        # scan ran. Detection finds lies the swarm has never seen; sanitisation
        # recognises ones it has already learned about, and needs no model call.
        for claim_id, claim_text in zip(result.claim_ids, result.claim_texts):
            verdict = self.sanitizer.judge(claim_id, claim_text)
            top = verdict.best_conflict
            result.verdicts.append(
                {
                    "claim_id": claim_id,
                    "verdict": verdict.verdict,
                    "elapsed_ms": round(verdict.elapsed_ms, 3),
                    "fact": top["fact"] if top else None,
                    "overlap": top["overlap"] if top else None,
                }
            )
        return result

    # ------------------------------------------------------- immune response

    def dispatch_antibodies(self) -> list[AntibodyResult]:
        """Respond to every active outbreak, then re-check the graph.

        The re-check matters: a fact learned now can condemn a claim that was
        merely unsupported a moment ago. Claims already quarantined or suspect
        are skipped, so nothing is weakened.
        """
        results = self.antibody.respond_to_all()
        if results:
            self.sanitizer.rejudge_unknown()
        return results

    def recall(self, text: str, embedding: list[float] | None = None) -> RecallResult:
        return self.scanner.recall(text, embedding)

    def contain_repeat(self, claim_id: str, recall: RecallResult):
        return self.scanner.contain_repeat(claim_id, recall)

    # ------------------------------------------------------------- reporting

    def trust_table(self) -> list[dict]:
        return self.scanner.trust_table()

    def infections(self) -> list[dict]:
        return self.scanner.active_infections()

    def effectiveness(self) -> list[dict]:
        return self.scanner.antibody_effectiveness()

    # ------------------------------------------------------- kernel reporting

    def kernel_facts(self) -> list[dict]:
        return [
            {
                "id": fact.id,
                "text": fact.text,
                "trust_level": fact.trust_level,
                "version": fact.version,
                "created_at": fact.created_at,
                "source": fact.source,
            }
            for fact in self.kernel.facts()
        ]

    def kernel_stats(self) -> dict:
        return self.kernel.stats()

    def kernel_conflicts(self) -> list[dict]:
        return self.kernel.conflicts()

    def fact_provenance(self, fact_id: str) -> dict | None:
        return self.kernel.provenance(fact_id)

    def sanitizer_summary(self) -> dict:
        return self.sanitizer.summary()

    @staticmethod
    def _node_view(node, labels) -> tuple[str, dict]:
        props = dict(getattr(node, "properties", {}) or {})
        node_id = str(props.get("id") or getattr(node, "id", ""))
        return node_id, {
            "id": node_id,
            "label": (labels or ["?"])[0],
            "title": props.get("text") or props.get("name") or node_id,
            "properties": props,
        }

    def graph_snapshot(self) -> dict:
        """Nodes and edges for the UI canvas."""
        rows = self.store.rows(
            """
            MATCH (n)
            OPTIONAL MATCH (n)-[r]->(m)
            RETURN n, labels(n), r, type(r), m, labels(m)
            """
        )
        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        for node, n_labels, rel, r_type, other, m_labels in rows:
            source_id = None
            if node is not None:
                source_id, view = self._node_view(node, n_labels)
                nodes[source_id] = view
            if rel is not None and other is not None and source_id is not None:
                target_id, target_view = self._node_view(other, m_labels)
                nodes.setdefault(target_id, target_view)
                edges.append({"source": source_id, "target": target_id, "label": r_type})
        return {"nodes": list(nodes.values()), "edges": edges}

    def stats(self) -> dict:
        counts = {}
        for label in (
            "Agent", "Claim", "KernelFact", "Source", "Infection",
            "Antibody", "Message", "Session",
        ):
            counts[label] = int(
                self.store.scalar(f"MATCH (n:{label}) RETURN count(n)") or 0
            )
        return counts
