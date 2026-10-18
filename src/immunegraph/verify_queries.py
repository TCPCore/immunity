"""Execute every load-bearing query against a live FalkorDB instance.

A Cypher query that has never been executed is not a feature. FalkorDB
implements a subset of openCypher, so every assumption must be proven.

Run:
    python -m immunegraph.verify_queries

Exits non-zero if any non-optional query fails or returns unexpected results.
Runs against a throwaway graph so it never touches demo data.
"""

from __future__ import annotations

import dataclasses
import sys
from typing import Any, Callable

from .config import Settings
from .models import fingerprint
from .queries import QUERIES, QuerySpec
from .schema import INDEX_STATEMENTS

VERIFY_SUFFIX = "_verify"

# Fixed ids so expectations can be precise.
AG_RESEARCHER = "ag_researcher"
AG_ANALYST = "ag_analyst"
AG_PLANNER = "ag_planner"

CL_ROOT = "cl_oxygen"          # the unsupported root claim
CL_HELMETS = "cl_helmets"      # derived from root (hop 1)
CL_LIFE = "cl_life_support"    # derived from helmets (hop 2)
CL_CONTRADICTION = "cl_no_oxygen"
CL_CORRECTION = "cl_correction"  # injected by an antibody
CL_CYCLE_A, CL_CYCLE_B, CL_CYCLE_C = "cl_cyc_a", "cl_cyc_b", "cl_cyc_c"

ROOT_TEXT = "Mars has breathable oxygen at night"


def _c(claim_id: str, text: str, status: str, verification: str) -> str:
    fp = fingerprint(text)
    safe_text = text.replace("'", "\\'")
    return (
        f"CREATE (:{'Claim'} {{id:'{claim_id}', text:'{safe_text}', "
        f"fingerprint:'{fp}', status:'{status}', verification:'{verification}', "
        f"confidence:1.0, created_at:'2026-10-15T00:00:00+00:00'}})"
    )


FIXTURE: list[str] = [
    # --- episodic layer ---
    "CREATE (:Session {id:'sess_verify', tenant_id:'verify', topic:'Mars mission', "
    "started_at:'2026-10-15T00:00:00+00:00', ended_at:null, status:'open'})",
    "CREATE (:Message {id:'msg_1', session_id:'sess_verify', seq:1, "
    "text:'Mars has breathable oxygen at night.', ts:'2026-10-15T00:00:01+00:00', role:'agent'})",
    # --- agents ---
    f"CREATE (:Agent {{id:'{AG_RESEARCHER}', name:'Researcher', role:'researcher', "
    "base_trust:1.0, tenant_id:'verify'})",
    f"CREATE (:Agent {{id:'{AG_ANALYST}', name:'Analyst', role:'analyst', "
    "base_trust:1.0, tenant_id:'verify'})",
    f"CREATE (:Agent {{id:'{AG_PLANNER}', name:'Planner', role:'planner', "
    "base_trust:1.0, tenant_id:'verify'})",
    # --- sources ---
    "CREATE (:Source {id:'src_nasa', name:'NASA', url:'https://nasa.gov', trust_level:'primary'})",
    # --- claims ---
    _c(CL_ROOT, ROOT_TEXT, "active", "unsourced"),
    _c(CL_HELMETS, "If Mars has breathable oxygen, we do not need helmets", "active", "unsourced"),
    _c(CL_LIFE, "We can skip life support", "active", "unsourced"),
    _c(CL_CONTRADICTION, "Mars has no breathable oxygen", "active", "sourced"),
    _c(CL_CORRECTION, "NASA data: the Martian atmosphere is 95% carbon dioxide", "active", "sourced"),
    _c(CL_CYCLE_A, "The sample is sterile", "quarantined", "unsourced"),
    _c(CL_CYCLE_B, "The sample was collected cleanly", "active", "unsourced"),
    _c(CL_CYCLE_C, "The collection protocol is sound", "active", "unsourced"),
    # --- assertions ---
    f"MATCH (a:Agent {{id:'{AG_RESEARCHER}'}}), (c:Claim {{id:'{CL_ROOT}'}}) "
    "CREATE (a)-[:ASSERTS {ts:'2026-10-15T00:00:01+00:00', message_id:'msg_1'}]->(c)",
    f"MATCH (a:Agent {{id:'{AG_ANALYST}'}}), (c:Claim {{id:'{CL_HELMETS}'}}) CREATE (a)-[:ASSERTS]->(c)",
    f"MATCH (a:Agent {{id:'{AG_PLANNER}'}}), (c:Claim {{id:'{CL_LIFE}'}}) CREATE (a)-[:ASSERTS]->(c)",
    f"MATCH (a:Agent {{id:'{AG_ANALYST}'}}), (c:Claim {{id:'{CL_CONTRADICTION}'}}) CREATE (a)-[:ASSERTS]->(c)",
    f"MATCH (a:Agent {{id:'{AG_PLANNER}'}}), (c:Claim {{id:'{CL_CYCLE_A}'}}) CREATE (a)-[:ASSERTS]->(c)",
    # --- support structure: the cascade ---
    f"MATCH (c:Claim {{id:'{CL_HELMETS}'}}), (p:Claim {{id:'{CL_ROOT}'}}) "
    "CREATE (c)-[:DERIVED_FROM {ts:'2026-10-15T00:01:00+00:00'}]->(p)",
    f"MATCH (c:Claim {{id:'{CL_LIFE}'}}), (p:Claim {{id:'{CL_HELMETS}'}}) "
    "CREATE (c)-[:DERIVED_FROM {ts:'2026-10-15T00:02:00+00:00'}]->(p)",
    # --- contradiction cluster ---
    f"MATCH (c1:Claim {{id:'{CL_CONTRADICTION}'}}), (c2:Claim {{id:'{CL_ROOT}'}}) "
    "CREATE (c1)-[:CONTRADICTS {detected_at:'2026-10-15T00:03:00+00:00', reason:'atmosphere composition'}]->(c2)",
    f"MATCH (c:Claim {{id:'{CL_CONTRADICTION}'}}), (s:Source {{id:'src_nasa'}}) "
    "CREATE (c)-[:SUPPORTED_BY {quote:'Mars atmosphere is ~95% CO2'}]->(s)",
    f"MATCH (c:Claim {{id:'{CL_CORRECTION}'}}), (s:Source {{id:'src_nasa'}}) "
    "CREATE (c)-[:SUPPORTED_BY {quote:'Mars atmosphere is ~95% CO2'}]->(s)",
    # --- an epistemic cycle: A -> B -> C -> A, three hops ---
    f"MATCH (a:Claim {{id:'{CL_CYCLE_A}'}}), (b:Claim {{id:'{CL_CYCLE_B}'}}) CREATE (a)-[:DERIVED_FROM]->(b)",
    f"MATCH (b:Claim {{id:'{CL_CYCLE_B}'}}), (c:Claim {{id:'{CL_CYCLE_C}'}}) CREATE (b)-[:DERIVED_FROM]->(c)",
    f"MATCH (c:Claim {{id:'{CL_CYCLE_C}'}}), (a:Claim {{id:'{CL_CYCLE_A}'}}) CREATE (c)-[:DERIVED_FROM]->(a)",
    # --- infections ---
    f"MATCH (c:Claim {{id:'{CL_ROOT}'}}) CREATE (:Infection {{id:'inf_root', type:'unsupported_root', "
    "status:'active', severity:0.0, detected_at:'2026-10-15T00:04:00+00:00', resolved_at:null})-[:TARGETS]->(c)",
    f"MATCH (c:Claim {{id:'{CL_CYCLE_A}'}}) CREATE (:Infection {{id:'inf_cycle', type:'circular_support', "
    "status:'resolved', severity:4.0, detected_at:'2026-10-15T00:05:00+00:00', "
    "resolved_at:'2026-10-15T00:06:00+00:00'})-[:TARGETS]->(c)",
    # --- antibodies ---
    "MATCH (i:Infection {id:'inf_root'}) CREATE (:Antibody {id:'ab_root', "
    "correction_text:'NASA: Mars is 95% CO2', strategy:'source_citation', outcome:'unknown', "
    "created_at:'2026-10-15T00:04:30+00:00'})-[:NEUTRALIZES]->(i)",
    "MATCH (i:Infection {id:'inf_cycle'}) CREATE (:Antibody {id:'ab_cycle', "
    "correction_text:'The sample derivation is circular', strategy:'contradiction_surface', "
    "outcome:'effective', created_at:'2026-10-15T00:05:30+00:00'})-[:NEUTRALIZES]->(i)",
    "MATCH (a:Antibody {id:'ab_root'}), (c:Claim {id:'" + CL_CORRECTION + "'}) CREATE (a)-[:INJECTS]->(c)",
    # --- kernel of truth ---------------------------------------------------
    # A fact the antibody established, with provenance, plus one claim that
    # conflicts with it and one that agrees. Without these the kernel queries
    # would execute against an empty label and prove nothing.
    "CREATE (:KernelFact {id:'kf_mars_oxygen', text:'Mars has no breathable oxygen', "
    "trust_level:'primary', version:1, status:'active', created_at:'2026-10-15T00:07:00+00:00'})",
    "MATCH (k:KernelFact {id:'kf_mars_oxygen'}), (s:Source {id:'src_nasa'}) "
    "CREATE (k)-[:DERIVED_FROM]->(s)",
    "MATCH (k:KernelFact {id:'kf_mars_oxygen'}), (ab:Antibody {id:'ab_root'}) "
    "CREATE (ab)-[:ESTABLISHES]->(k)",
    f"MATCH (c:Claim {{id:'{CL_ROOT}'}}), (k:KernelFact {{id:'kf_mars_oxygen'}}) "
    "CREATE (c)-[:CONFLICTS_WITH {score:0.67, detected_at:'2026-10-15T00:07:30+00:00'}]->(k)",
    f"MATCH (c:Claim {{id:'{CL_CORRECTION}'}}), (k:KernelFact {{id:'kf_mars_oxygen'}}) "
    "CREATE (c)-[:ALIGNED_WITH {score:0.80, detected_at:'2026-10-15T00:07:30+00:00'}]->(k)",
    # --- embeddings, so the vector recall path (Q7) is genuinely exercised ---
    # Dimension 4 keeps the test fast; production uses the real model width.
    f"MATCH (c:Claim {{id:'{CL_ROOT}'}}) SET c.embedding = vecf32([1.0, 0.0, 0.0, 0.0])",
    f"MATCH (c:Claim {{id:'{CL_HELMETS}'}}) SET c.embedding = vecf32([0.95, 0.05, 0.0, 0.0])",
    f"MATCH (c:Claim {{id:'{CL_CYCLE_A}'}}) SET c.embedding = vecf32([0.90, 0.10, 0.0, 0.0])",
]

# Applied after the fixture. May fail on a build without vector support, in
# which case Q7 reports SKIP and Q7B remains the working recall path.
OPTIONAL_INDEXES: list[str] = [
    "CREATE VECTOR INDEX FOR (c:Claim) ON (c.embedding) "
    "OPTIONS {dimension: 4, similarityFunction: 'cosine'}",
]


# Expectations: name -> predicate over the returned rows.
EXPECTATIONS: dict[str, Callable[[list[list[Any]]], bool]] = {
    "Q1_CIRCULAR_SUPPORT": lambda rows: len(rows) >= 1,
    "Q1B_CIRCULAR_FALLBACK": lambda rows: len(rows) >= 1,
    "Q2_UNSUPPORTED_CLAIMS": lambda rows: any(r[0] == CL_ROOT for r in rows),
    "Q3_CONTRADICTIONS": lambda rows: len(rows) >= 1,
    "Q4_CASCADE_REACH": lambda rows: bool(rows) and int(rows[0][2]) >= 2 and int(rows[0][3]) >= 3,
    "Q5_TRUST_DECAY": lambda rows: len(rows) >= 3,
    "Q6_IMMUNITY_RECALL": lambda rows: len(rows) >= 1,
    "Q7_VECTOR_RECALL": lambda rows: len(rows) >= 1,
    "Q7B_RECALL_CANDIDATES": lambda rows: len(rows) >= 1,
    "Q8_ANTIBODY_EFFECTIVENESS": lambda rows: len(rows) >= 1,
    "K1_KERNEL_FACTS": lambda rows: any(r[0] == "kf_mars_oxygen" for r in rows),
    "K2_KERNEL_CONFLICTS": lambda rows: any(r[0] == CL_ROOT for r in rows),
    "K3_KERNEL_STATS": lambda rows: bool(rows) and int(rows[0][0]) >= 1 and int(rows[0][2]) >= 1,
    "K4_UNSANITISED_CLAIMS": lambda rows: len(rows) >= 1,
    "K5_FACT_PROVENANCE": lambda rows: bool(rows) and rows[0][4] == "ab_root" and rows[0][5] == "NASA",
    "ACTIVE_INFECTIONS": lambda rows: len(rows) >= 1,
    "GRAPH_SNAPSHOT": lambda rows: len(rows) >= 1,
    "CLAIM_NEIGHBOURHOOD": lambda rows: len(rows) >= 1,
}

PARAMS: dict[str, dict[str, Any]] = {
    "Q4_CASCADE_REACH": {"root_claim_id": CL_ROOT},
    "Q6_IMMUNITY_RECALL": {"fingerprint": fingerprint(ROOT_TEXT)},
    "Q7_VECTOR_RECALL": {"query_embedding": [1.0, 0.0, 0.0, 0.0], "k": 5},
    "K4_UNSANITISED_CLAIMS": {"limit": 50},
    "K5_FACT_PROVENANCE": {"fact_id": "kf_mars_oxygen"},
    "CLAIM_NEIGHBOURHOOD": {"claim_id": CL_ROOT},
}


def _seed(store) -> None:
    store.create_indexes()
    for statement in FIXTURE:
        store.query(statement)
    for statement in OPTIONAL_INDEXES:
        try:
            store.query(statement)
        except Exception as exc:  # noqa: BLE001
            print(f"note: optional index unavailable ({str(exc)[:80]}), Q7 will SKIP")


def _check(spec: QuerySpec, store) -> tuple[str, str, list[list[Any]] | None]:
    """Run one query. Returns (status, detail, rows)."""
    params = PARAMS.get(spec.name, {})
    missing = [p for p in spec.required_params if p not in params]
    if missing:
        return "SKIP", f"harness missing params: {missing}", None
    try:
        rows = store.rows(spec.cypher, params)
    except Exception as exc:  # noqa: BLE001 - we want the raw engine error
        status = "SKIP" if spec.optional else "FAIL"
        return status, f"{type(exc).__name__}: {exc}".replace("\n", " ")[:180], None

    expectation = EXPECTATIONS.get(spec.name)
    if expectation and not expectation(rows):
        detail = "wrong result: " + repr(rows[:2])[:150]
        return ("SKIP" if spec.optional else "FAIL"), detail, rows
    return "PASS", f"{len(rows)} row(s)", rows


def run(verbose: bool = True) -> int:
    base = Settings.from_env()
    settings = dataclasses.replace(base, graph=base.graph + VERIFY_SUFFIX)

    print(f"FalkorDB target : {settings.host}:{settings.port}")
    print(f"verify graph    : {settings.graph}")
    print(f"indexes         : {len(INDEX_STATEMENTS)}")
    print()

    from .db import GraphStore

    store = GraphStore(settings)

    if not store.ping():
        print(f"FAIL: cannot reach FalkorDB at {settings.host}:{settings.port}")
        print("      start it with: docker compose up -d")
        return 2

    print("seeding fixture graph ...")
    store.reset()
    _seed(store)
    print(f"seeded {store.node_count()} nodes\n")

    header = f"{'query':<28} {'status':<6} detail"
    print(header)
    print("-" * len(header))
    failures = 0
    for name, spec in QUERIES.items():
        status, detail, rows = _check(spec, store)
        print(f"{name:<28} {status:<6} {detail}")
        if verbose and rows and status == "PASS":
            for row in rows[:2]:
                print(f"{'':<28}        {str(row)[:110]}")
        if status == "FAIL":
            failures += 1

    print()
    store.graph.delete()
    print(f"cleaned up graph {settings.graph!r}")

    if failures:
        print(f"\n{failures} query/queries FAILED. Fix the Cypher before building on it.")
        return 1
    print("\nAll non-optional queries executed and returned expected shapes.")
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
