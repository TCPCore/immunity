"""A company brain with an immune system.

Track 03 asks for a shared brain over a company's connected knowledge, with
reasoning a person can trace. The characteristic failure there is not retrieval
failure. It is that one agent's confident wrong answer gets repeated by three
other agents until it becomes company doctrine — and a wrong *price* is a
million-dollar hallucination, not an embarrassing paragraph.

That is the same pathology ImmuneGraph already models, with the company's own
documents as the kernel of truth. This module supplies the corpus and the
scenario; the machinery is unchanged.

The corpus is synthetic (rule 6 permits synthetic data), committed under
``data/company/`` so the demo is reproducible and no private information is
anywhere near it.

Two things this adds beyond the Mars scenario:

*Quantity conflicts.* A company brain is mostly numbers — prices, SLAs, limits,
retention windows. "Enterprise is 399 USD per seat" and "Enterprise costs 499
USD per seat" agree on every word except the one that matters, so negation-based
conflict detection scores them as *aligned*. ``conflicts_on_numbers`` closes
that, and without it the sanitiser would have blessed a wrong price as policy.

*Policy drift.* Facts are versioned. When a price changes the old fact is
superseded rather than deleted, and an agent still quoting the old price is
caught against the new one. A company brain that cannot forget is as dangerous
as one that cannot learn.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from .models import Agent, Antibody, Claim, Infection, Source
from .schema import (
    INFECTION_CONTAINED,
    INFECTION_CONTRADICTION,
    TRUST_PRIMARY,
    TRUST_REPUTABLE,
)

COMPANY = "Halcyon Robotics"


def _short() -> str:
    return f"c{uuid4().hex[:8]}"


@dataclass(frozen=True)
class Document:
    """One authoritative company document, and the facts it establishes."""

    id: str
    title: str
    department: str
    path: str
    facts: tuple[str, ...]


DOCUMENTS: tuple[Document, ...] = (
    Document(
        id="doc_pricing",
        title="Enterprise and Team pricing (2026)",
        department="Sales",
        path="data/company/pricing.md",
        facts=(
            "The Enterprise plan costs 499 USD per seat per year",
            "The Team plan costs 99 USD per seat per year",
        ),
    ),
    Document(
        id="doc_refunds",
        title="Refund policy",
        department="Support",
        path="data/company/refunds.md",
        facts=("Refunds are available within 30 days of purchase",),
    ),
    Document(
        id="doc_sla",
        title="Support SLA",
        department="Support",
        path="data/company/sla.md",
        facts=("Critical incidents are acknowledged within 15 minutes",),
    ),
    Document(
        id="doc_residency",
        title="Data residency",
        department="Engineering",
        path="data/company/data-residency.md",
        facts=("Customer data for EU customers is stored in the EU",),
    ),
)

DEPARTMENTS: tuple[tuple[str, str], ...] = (
    ("ag_sales", "Sales"),
    ("ag_support", "Support"),
    ("ag_finance", "Finance"),
)

# The wrong price an agent invents, and the question that provokes it.
WRONG_PRICE = "The Enterprise plan is 399 USD per seat per year."
WRONG_PRICE_REPEAT = "Enterprise customers pay 399 USD per seat per year."
PRICE_QUESTION = "What does the Enterprise plan cost?"
OLD_PRICE = "The Enterprise plan costs 499 USD per seat per year."


class CompanyBrain:
    """Seeds a company's documents into the kernel, then runs the scenario."""

    def __init__(self, swarm) -> None:
        self.swarm = swarm
        self.store = swarm.store
        self.writer = swarm.writer
        self.kernel = swarm.kernel
        self.sanitizer = swarm.sanitizer
        self.scanner = swarm.scanner

    # ------------------------------------------------------------------ seeding

    def seeded(self) -> bool:
        """Are the company's documents loaded?

        Deliberately not "is the kernel empty" — the kernel may already hold
        facts from another domain, and testing emptiness meant the documents were
        never seeded in a graph that also ran the Mars scenario.
        """
        return bool(
            self.store.rows("MATCH (s:Source {id: 'doc_pricing'}) RETURN s.id")
        )

    def seed(self) -> dict:
        """Load the company's documents as sources and their facts as truth.

        One source per document, one KernelFact per fact, each linked to the
        document it came from. Provenance is therefore a traversal rather than a
        citation string a model wrote from memory.
        """
        facts = 0
        for doc in DOCUMENTS:
            source = Source(
                id=doc.id,
                name=f"{COMPANY}: {doc.title}",
                url=doc.path,
                trust_level=TRUST_PRIMARY,
            )
            self.writer.upsert_source(source)
            for text in doc.facts:
                self.kernel.register_fact(
                    text, source=source, trust_level=TRUST_PRIMARY
                )
                facts += 1

        for agent_id, name in DEPARTMENTS:
            self.writer.upsert_agent(
                Agent(id=agent_id, name=name, role="department", base_trust=1.0)
            )

        return {"documents": len(DOCUMENTS), "facts": facts, "departments": len(DEPARTMENTS)}

    # ------------------------------------------------------------------ talking

    def _say(self, agent_id: str, text: str) -> str:
        """Record an agent's statement as a claim. Returns the claim id."""
        session = self.swarm._require_session()
        message_id, _seq = self.writer.record_message(session, agent_id, text)
        claim_id = self.swarm._new_claim_id()
        self.writer.record_claim(
            Claim(id=claim_id, text=text, confidence=0.9), agent_id, session, message_id
        )
        return claim_id

    def revise_price(self, new_price: int = 549) -> dict:
        """Publish a new price, superseding the old fact instead of deleting it.

        History is kept, so "when did we stop believing this" is answerable. The
        old fact simply stops being active.
        """
        new_text = f"The Enterprise plan costs {new_price} USD per seat per year"

        # register_fact does not deduplicate, so guard here: running the scenario
        # twice must not create a second identical fact.
        existing = [fact for fact in self.kernel.facts() if fact.text == new_text]
        if existing:
            return {"old_fact": None, "new_fact": existing[0].id, "text": new_text,
                    "stale_claims": [], "unchanged": True}

        # Exclude the new text: running the scenario twice must not supersede
        # the current price with itself.
        old = [
            fact
            for fact in self.kernel.facts()
            if "Enterprise plan costs" in fact.text and fact.text != new_text
        ]
        old_id = old[0].id if old else None
        new_id = self.kernel.register_fact(
            new_text, source="doc_pricing", trust_level=TRUST_PRIMARY
        )
        if old_id:
            self.kernel.supersede(old_id, new_id)

        # Re-judge everything still active against the new truth. Without this a
        # claim that was correct last week stays blessed forever, and a brain
        # that cannot forget is as dangerous as one that cannot learn.
        stale: list[str] = []
        for claim_id, text in self.store.rows(
            "MATCH (c:Claim) WHERE c.status = 'active' RETURN c.id, c.text"
        ):
            if self.sanitizer.judge(claim_id, text).conflicts:
                stale.append(claim_id)

        return {
            "old_fact": old_id,
            "new_fact": new_id,
            "text": new_text,
            "stale_claims": stale,
        }

    # ----------------------------------------------------------------- scenario

    def scenario(self):
        """A company brain getting a price wrong, and being corrected.

        A generator, so the UI can show each step as it lands.
        """
        if self.swarm.session_id is None:
            self.swarm.start(topic=f"{COMPANY} knowledge", session_id="session_company")
        self.swarm._require_session()

        if not self.seeded():
            counts = self.seed()
            yield {
                "phase": "seeded",
                "message": (
                    f"Loaded {counts['documents']} company documents into the kernel "
                    f"as {counts['facts']} facts."
                ),
            }
        else:
            yield {"phase": "seeded", "message": "Company documents already in the kernel."}

        facts = self.kernel.facts()

        # 1. Sales answers a pricing question from memory, and gets it wrong.
        wrong_id = self._say("ag_sales", WRONG_PRICE)
        verdict = self.sanitizer.judge(wrong_id, WRONG_PRICE, facts)
        yield {
            "phase": "conflict",
            "claim_id": wrong_id,
            "message": (
                f"Sales answers {PRICE_QUESTION!r} with {WRONG_PRICE!r} — the sanitiser "
                + (
                    f"finds a policy conflict in {verdict.elapsed_ms:.1f}ms."
                    if verdict.conflicts
                    else f"found no conflict ({verdict.elapsed_ms:.1f}ms)."
                )
            ),
            "conflict": bool(verdict.conflicts),
        }

        # 2. Support repeats it. This is how a wrong price becomes doctrine.
        repeat_id = self._say("ag_support", WRONG_PRICE_REPEAT)
        # Support is not independently wrong — it is repeating a colleague. That
        # edge is what makes the spread a cascade rather than two coincidences.
        self.writer.link_derivation(repeat_id, wrong_id)
        repeat_verdict = self.sanitizer.judge(repeat_id, WRONG_PRICE_REPEAT, facts)
        yield {
            "phase": "spread",
            "claim_id": repeat_id,
            "message": (
                "Support repeats it to a customer — the wrong price is now spreading."
                if repeat_verdict.conflicts
                else "Support repeats it, but the sanitiser did not flag the restatement."
            ),
            "conflict": bool(repeat_verdict.conflicts),
        }

        # 3. An infection opens, sized by how far the claim reached.
        infected, exposed = self.scanner.cascade_reach(wrong_id)
        severity = float(1 + infected + exposed)
        infection = Infection(
            id=f"{_short()}_co",
            type=INFECTION_CONTRADICTION,
            status="active",
            severity=severity,
        )
        self.writer.record_infection(infection)
        self.writer.link_infection_target(infection.id, wrong_id)
        self.writer.link_infection_target(infection.id, repeat_id)
        yield {
            "phase": "infected",
            "message": f"Infection opened across two departments — severity {severity:.1f}.",
            "severity": severity,
        }

        # 4. The antibody cites the pricing document, not a memory of it.
        pricing = next(doc for doc in DOCUMENTS if doc.id == "doc_pricing")
        correction = Claim(
            id=f"{_short()}_fix",
            text=pricing.facts[0],
            verification="sourced",
        )
        self.writer.record_claim(
            correction, agent_id=self.swarm.antibody.ensure_system_agent()
        )
        self.writer.link_source(
            correction.id,
            pricing.id,
            quote="| Enterprise | 499 USD per seat per year | Dedicated support, SSO, audit log |",
        )
        antibody = Antibody(
            id=f"{_short()}_ab",
            correction_text=(
                "Enterprise is 499 USD per seat per year, per the 2026 pricing sheet."
            ),
            strategy="policy_citation",
            outcome="effective",
        )
        self.writer.record_antibody(antibody, infection.id)
        self.writer.link_antibody_injection(antibody.id, correction.id)

        for claim_id in (wrong_id, repeat_id):
            self.writer.set_status(claim_id, "quarantined")
            self.writer.set_verification(claim_id, "contradicted")
        self.writer.set_infection_status(infection.id, INFECTION_CONTAINED)
        yield {
            "phase": "arrested",
            "message": (
                f"Quarantined both. The antibody cites {pricing.title} and injects "
                f"the correct price; severity {severity:.1f} contained."
            ),
        }

        # 5. The price changes. The old fact is superseded, not deleted.
        revised = self.revise_price(549)
        yield {
            "phase": "revised",
            "message": (
                "Finance raises the price to 549 USD. The old fact is SUPERSEDED, "
                "not deleted — so history stays answerable."
            ),
            "revision": revised,
        }

        # 6. An agent quotes the price that used to be correct.
        stale_id = self._say("ag_sales", OLD_PRICE)
        stale_verdict = self.sanitizer.judge(stale_id, OLD_PRICE)
        yield {
            "phase": "stale",
            "claim_id": stale_id,
            "message": (
                "Sales quotes the old 499 price — correct last week, wrong now. "
                f"The sanitiser catches it against the new fact in "
                f"{stale_verdict.elapsed_ms:.1f}ms."
            ),
            "conflict": bool(stale_verdict.conflicts),
        }

        yield {
            "phase": "done",
            "message": (
                f"{len(self.kernel.facts())} facts in force, "
                f"{self.kernel.count() - len(self.kernel.facts())} superseded, "
                f"history intact."
            ),
        }
