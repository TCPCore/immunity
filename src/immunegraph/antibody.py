"""The antibody agent: the immune response.

Given an Infection, the antibody decides *how* to respond based on what the
graph shows, injects a sourced correction as a first-class Claim, quarantines
the offending claim, and records the episode so the same infection is never
re-diagnosed.

Two correction strategies:

  * ``source_citation`` — we have a trusted counter-fact, so cite it.
  * ``contradiction_surface`` — we have none, so state plainly that the claim
    is unsupported and how much depends on it, rather than inventing a fact.

The second strategy is the honest fallback. An antibody that fabricates a
counter-fact would be a new infection.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field

from .memory import MemoryWriter
from .models import Antibody, Claim, Source, utcnow
from .schema import (
    CLAIM_QUARANTINED,
    CLAIM_SUSPECT,
    INFECTION_CONTAINED,
    INFECTION_UNSUPPORTED,
    TRUST_PRIMARY,
)

STRATEGY_CITATION = "source_citation"
STRATEGY_SURFACE = "contradiction_surface"
STRATEGY_QUARANTINE = "quarantine_only"

# A small, honest knowledge base of trusted counter-facts for the demo
# scenario. Every entry carries a real, public source URL.
CORRECTION_KB: list[dict] = [
    {
        "match": {"mars", "oxygen"},
        "correction": (
            "NASA: the Martian atmosphere is about 95% carbon dioxide with only trace "
            "oxygen, so there is no breathable oxygen on Mars at any time of day."
        ),
        "source": {
            "name": "NASA",
            "url": "https://science.nasa.gov/mars/facts/",
            "trust_level": TRUST_PRIMARY,
        },
    },
    {
        "match": {"mars", "atmosphere"},
        "correction": (
            "NASA: the Martian atmosphere is roughly 95% carbon dioxide at very low "
            "pressure, around 1% of Earth's surface pressure."
        ),
        "source": {
            "name": "NASA",
            "url": "https://science.nasa.gov/mars/facts/",
            "trust_level": TRUST_PRIMARY,
        },
    },
]


@dataclass
class AntibodyResult:
    antibody_id: str
    infection_id: str
    root_claim_id: str
    root_claim_text: str
    correction_text: str
    correction_claim_id: str
    source_name: str | None
    strategy: str
    severity_before: float
    contained: bool = True
    cascade_contained: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        out = [
            f"antibody {self.antibody_id} responding to infection {self.infection_id}",
            f"  target      : {self.root_claim_text!r}",
            f"  strategy    : {self.strategy}",
            f"  correction  : {self.correction_text!r}",
        ]
        if self.source_name:
            out.append(f"  cited source: {self.source_name}")
        out.append(f"  severity    : {self.severity_before:.1f}")
        out.append("  action      : root claim quarantined, infection contained")
        if self.cascade_contained:
            out.append(
                f"  cascade     : {len(self.cascade_contained)} downstream claim(s) "
                "marked suspect (support invalidated)"
            )
        return out


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class AntibodyAgent:
    """Generates and injects corrections."""

    def __init__(self, store, writer: MemoryWriter | None = None, client=None) -> None:
        self.store = store
        self.writer = writer or MemoryWriter(store)
        self._client = client
        self.model = os.getenv("LLM_MODEL", "gpt-4o-mini")
        self.system_agent_id = "agent_antibody"

    def ensure_system_agent(self) -> str:
        """The antibody system is itself an actor in the graph.

        Without this node the correction claim would have no asserting agent,
        and ``record_claim`` would silently skip creating the ASSERTS edge.
        """
        from .models import Agent

        self.writer.upsert_agent(
            Agent(
                id=self.system_agent_id,
                name="Antibody System",
                role="immune",
                base_trust=1.0,
            )
        )
        return self.system_agent_id

    # ------------------------------------------------------------ strategy

    @staticmethod
    def _kb_lookup(text: str) -> dict | None:
        from .models import token_set

        tokens = token_set(text)
        for entry in CORRECTION_KB:
            if entry["match"] <= tokens:
                return entry
        return None

    def load_infection(self, infection_id: str) -> dict | None:
        row = self.store.one(
            """
            MATCH (i:Infection {id: $iid})-[:TARGETS]->(c:Claim)
            OPTIONAL MATCH (i)-[:SPREAD_TO]->(spread:Claim)
            RETURN i.id, i.type, i.severity, i.status,
                   c.id, c.text, c.fingerprint,
                   count(DISTINCT spread)
            """,
            {"iid": infection_id},
        )
        if not row:
            return None
        return {
            "infection_id": row[0], "type": row[1], "severity": float(row[2] or 0),
            "status": row[3], "root_claim_id": row[4], "root_claim_text": row[5],
            "fingerprint": row[6], "spread_count": int(row[7] or 0),
        }

    def _llm_correction(self, claim_text: str, infection_type: str) -> dict | None:
        """Ask the model for a correction that MUST carry a citation."""
        try:
            if self._client is None:
                from openai import OpenAI

                self._client = OpenAI(
                    api_key=os.getenv("OPENAI_API_KEY") or "not-set",
                    base_url=os.getenv("OPENAI_BASE_URL") or None,
                )
            response = self._client.chat.completions.create(
                model=self.model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an antibody agent correcting a false claim made by an "
                            "AI agent. Return JSON: {\"correction\": str, \"source_name\": str, "
                            "\"source_url\": str, \"trust_level\": \"primary\"|\"reputable\"}. "
                            "Only cite a source that genuinely supports the correction. If no "
                            "trusted source exists, set source_name to null and simply state "
                            "that the claim is unsupported. Never invent a citation."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Infection type: {infection_type}\nFalse claim: {claim_text}",
                    },
                ],
            )
            return json.loads(response.choices[0].message.content or "{}")
        except Exception:  # noqa: BLE001 - fall back to the deterministic path
            return None

    def _decide(self, claim_text: str, infection_type: str) -> tuple[str, dict | None, str]:
        """Return (correction_text, source_dict_or_None, strategy)."""
        kb = self._kb_lookup(claim_text)
        if kb:
            return kb["correction"], kb["source"], STRATEGY_CITATION

        llm = self._llm_correction(claim_text, infection_type)
        if llm and llm.get("correction"):
            source = None
            if llm.get("source_name"):
                source = {
                    "name": llm["source_name"],
                    "url": llm.get("source_url") or "",
                    "trust_level": llm.get("trust_level") or "reputable",
                }
            strategy = STRATEGY_CITATION if source else STRATEGY_SURFACE
            return llm["correction"], source, strategy

        # Honest fallback: no trusted counter-fact is available, so do not
        # fabricate one. State the epistemic status instead.
        return (
            f"No trusted source supports the claim {claim_text!r}. It is quarantined "
            f"pending evidence, and anything derived from it should not be relied on.",
            None,
            STRATEGY_SURFACE,
        )

    # -------------------------------------------------------------- cascade

    def contain_cascade(self, root_claim_id: str) -> list[str]:
        """Mark claims whose only support descends from a quarantined root.

        Quarantining the root alone leaves the Analyst's and Planner's claims
        reading as 'believed', which is actively misleading: their entire
        justification was the claim we just refuted. They are downgraded to
        'suspect' — not proven false, but no longer trustworthy — unless they
        carry their own independent trusted source, in which case they stand on
        their own.

        Returns the ids that were downgraded.
        """
        rows = self.store.rows(
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
        return [row[0] for row in rows]

    # -------------------------------------------------------------- respond

    def respond(self, infection_id: str) -> AntibodyResult | None:
        """Diagnose, correct, quarantine, and record the episode."""
        info = self.load_infection(infection_id)
        if info is None:
            return None

        correction_text, source_info, strategy = self._decide(
            info["root_claim_text"], info["type"]
        )

        # 1. the correction enters the graph as a first-class Claim, and if it
        #    has a source it is *supported*, so Q2 will never flag it.
        correction = Claim(id=_new_id("cl"), text=correction_text, verification="unsourced")
        self.writer.record_claim(correction, agent_id=self.ensure_system_agent())
        source_name = None
        if source_info:
            source = Source(
                id=f"src_{source_info['name'].lower().replace(' ', '_')}",
                name=source_info["name"],
                url=source_info.get("url", ""),
                trust_level=source_info.get("trust_level", "unknown"),
            )
            self.writer.upsert_source(source)
            self.writer.link_source(correction.id, source.id, quote=correction_text)
            source_name = source.name

        # 2. the antibody itself
        antibody = Antibody(
            id=_new_id("ab"),
            correction_text=correction_text,
            strategy=strategy,
            outcome="unknown",
            created_at=utcnow(),
        )
        self.writer.record_antibody(antibody, infection_id)
        self.writer.link_antibody_injection(antibody.id, correction.id)

        # 3. assert the contradiction explicitly. We do not rely on lexical
        #    detection here: if the antibody says it refutes the claim, that is
        #    a deliberate, recorded assertion.
        if source_info:
            self.writer.link_contradiction(
                correction.id,
                info["root_claim_id"],
                reason=f"antibody correction citing {source_name}",
                update_verification=False,
            )

        # 4. quarantine the offender and close the outbreak
        self.writer.set_status(info["root_claim_id"], CLAIM_QUARANTINED)
        self.writer.set_verification(info["root_claim_id"], "contradicted")
        self.writer.set_infection_status(infection_id, INFECTION_CONTAINED)

        # 5. contain the whole cascade, not only its root
        cascade = self.contain_cascade(info["root_claim_id"])

        return AntibodyResult(
            antibody_id=antibody.id,
            infection_id=infection_id,
            root_claim_id=info["root_claim_id"],
            root_claim_text=info["root_claim_text"],
            correction_text=correction_text,
            correction_claim_id=correction.id,
            source_name=source_name,
            strategy=strategy,
            severity_before=info["severity"],
            cascade_contained=cascade,
        )

    def respond_to_all(self) -> list[AntibodyResult]:
        """Respond to every active outbreak, most severe first."""
        rows = self.store.rows(
            """
            MATCH (i:Infection)-[:TARGETS]->(c:Claim)
            WHERE i.status = 'active'
            RETURN i.id
            ORDER BY i.severity DESC, i.detected_at ASC
            """
        )
        results = []
        for row in rows:
            result = self.respond(row[0])
            if result:
                results.append(result)
        return results

    def mark_outcome(self, antibody_id: str, outcome: str) -> None:
        self.writer.set_antibody_outcome(antibody_id, outcome)
