"""Prove the project qualifies for each of the three tracks, against a live graph.

Rule 5 lets you enter any or all tracks, but naming a track is a claim. This
script runs the capability each track is actually about and reports pass or fail
with the numbers that justify it. Run it before submitting:

    python scripts/verify_tracks.py
"""

from __future__ import annotations

import dataclasses
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from immunegraph.agents import MARS_CASCADE  # noqa: E402
from immunegraph.company import DOCUMENTS, OLD_PRICE, WRONG_PRICE, CompanyBrain  # noqa: E402
from immunegraph.config import Settings  # noqa: E402
from immunegraph.db import GraphStore  # noqa: E402
from immunegraph.event_log import WriteLog  # noqa: E402
from immunegraph.swarm import Swarm  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(track: str, ok: bool, detail: str) -> None:
    RESULTS.append((track, bool(ok), detail))


def fresh(suffix: str) -> tuple[GraphStore, Swarm]:
    base = Settings.from_env()
    store = GraphStore(dataclasses.replace(base, graph=base.graph + suffix))
    store.reset()
    store.create_indexes()
    # An attached log: two of the checks below are about durability, and
    # say() alone only detects. Antibody dispatch is a separate step.
    swarm = Swarm(store, log=WriteLog(":memory:"))
    swarm.start("verify", f"s_{suffix.strip('_')}")
    return store, swarm


# --------------------------------------------------------------------- track 01

def track_01() -> None:
    """Agents That Act on Connected Data: detection leads to action on the graph."""
    store, swarm = fresh("_track01")
    try:
        for persona, text, _ in MARS_CASCADE:
            swarm.say(persona, text)
        # Detection is passive. This is the action half of the track.
        swarm.dispatch_antibodies()

        infections = swarm.infections()
        check("01", bool(infections), f"{len(infections)} infection(s) opened by detection")

        severities = [i["severity"] for i in infections]
        check("01", any(s > 1 for s in severities),
              f"severity computed from graph reach: {severities}")

        quarantined = store.scalar(
            "MATCH (c:Claim) WHERE c.status = 'quarantined' RETURN count(c)")
        check("01", int(quarantined) >= 1, f"{quarantined} claim(s) quarantined by an antibody")

        acted = store.scalar(
            "MATCH (a:Antibody)-[:INJECTS]->(:Claim) RETURN count(a)")
        check("01", int(acted) >= 1, f"{acted} antibody(ies) injected a correction into the graph")

        contained = store.scalar(
            "MATCH (i:Infection) WHERE i.status = 'contained' RETURN count(i)")
        check("01", int(contained) >= 1, f"{contained} infection(s) contained, not merely logged")

        suspect = store.scalar(
            "MATCH (c:Claim {status: 'suspect'}) RETURN count(c)")
        check("01", int(suspect) >= 1,
              f"{suspect} derived claim(s) invalidated by contain_cascade")
    finally:
        store.graph.delete()


# --------------------------------------------------------------------- track 02

def track_02() -> None:
    """Agent Memory and Coordination: three memory kinds, derived trust, durability."""
    store, swarm = fresh("_track02")
    try:
        for persona, text, _ in MARS_CASCADE:
            swarm.say(persona, text)
        swarm.dispatch_antibodies()

        episodic = store.scalar("MATCH (m:Message) RETURN count(m)")
        semantic = store.scalar("MATCH (c:Claim) RETURN count(c)")
        procedural = store.scalar("MATCH (ab:Antibody) RETURN count(ab)")
        check("02", int(episodic) > 0 and int(semantic) > 0 and int(procedural) > 0,
              f"episodic {episodic} · semantic {semantic} · procedural {procedural}")

        facts = swarm.kernel_stats()["facts"]
        check("02", facts >= 1, f"{facts} fact(s) in the kernel of truth")

        provenance = [
            swarm.fact_provenance(f["id"]) for f in swarm.kernel_facts()
        ]
        check("02", any(p and p.get("source") for p in provenance),
              "kernel facts carry provenance to the antibody and source that established them")

        # Trust must be derived, never stored: the node must not carry the value.
        stored = store.scalar(
            "MATCH (a:Agent) WHERE a.effective_trust IS NOT NULL RETURN count(a)")
        check("02", int(stored) == 0, "no agent stores its trust; it is computed by Q5")

        derived = swarm.trust_table()
        check("02", bool(derived) and all("effective_trust" in row for row in derived),
              f"trust derived for {len(derived)} agents from exposure: "
              + ", ".join(f"{r['agent']} {r['effective_trust']}" for r in derived))

        before = swarm.log_count()
        check("02", before > 0, f"{before} writes in the write-ahead log")

        rebuilt = swarm.rebuild_from_log()
        check("02", rebuilt.get("identical") is True,
              f"rebuild from log identical: {rebuilt.get('applied')} applied, "
              f"{rebuilt.get('failed')} failed")
    finally:
        store.graph.delete()


# --------------------------------------------------------------------- track 03

def track_03() -> None:
    """Company Brain: documents become truth, cross-department lies are caught."""
    store, swarm = fresh("_track03")
    try:
        brain = CompanyBrain(swarm)
        counts = brain.seed()
        check("03", counts["documents"] == len(DOCUMENTS),
              f"{counts['documents']} company documents loaded as Source nodes")

        sourced = store.scalar(
            "MATCH (k:KernelFact)-[:SUPPORTED_BY|CITES]->(:Source) RETURN count(DISTINCT k)")
        if not sourced:
            sourced = store.scalar(
                "MATCH (s:Source)<-[:CITES]-(k:KernelFact) RETURN count(DISTINCT k)")
        check("03", counts["facts"] >= 1, f"{counts['facts']} facts established by the documents")

        facts = swarm.kernel.facts()
        if facts:
            prov = swarm.kernel.provenance(facts[0].id) or {}
            check("03", bool(prov.get("source")),
                  f"fact provenance resolves to a document: {prov.get('source')!r}")

        # A wrong price must be caught against the policy document.
        claim_id = brain._say("ag_sales", WRONG_PRICE)
        verdict = swarm.sanitizer.judge(claim_id, WRONG_PRICE, facts)
        check("03", bool(verdict.conflicts),
              f"wrong price caught against policy in {verdict.elapsed_ms:.1f} ms")

        # Policy drift: the old fact is superseded and stale answers are re-judged.
        steps = [s["phase"] for s in brain.scenario()]
        check("03", "revised" in steps and "stale" in steps,
              "policy revision supersedes the old fact and re-judges active claims")

        active = [f.text for f in swarm.kernel.facts()]
        check("03", any("549" in t for t in active),
              "the revised price is the fact in force")
        kept = swarm.kernel.count() > len(active)
        check("03", kept, "the superseded fact is retained, so history stays answerable")

        stale = store.rows(
            "MATCH (c:Claim) WHERE c.text = $t RETURN c.status", {"t": OLD_PRICE})
        check("03", bool(stale) and stale[0][0] == "suspect",
              "the previously correct price is flagged after the change")
    finally:
        store.graph.delete()


def main() -> int:
    print("=" * 78)
    print("TRACK QUALIFICATION, measured against a live FalkorDB")
    print("=" * 78)

    started = time.perf_counter()
    for name, fn in (("01", track_01), ("02", track_02), ("03", track_03)):
        print(f"\nTrack {name}")
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            check(name, False, f"raised {type(exc).__name__}: {exc}")

    for track, ok, detail in RESULTS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {track}  {detail}")

    failed = [r for r in RESULTS if not r[1]]
    by_track = {}
    for track, ok, _ in RESULTS:
        by_track.setdefault(track, []).append(ok)

    print("\n" + "=" * 78)
    for track, outcomes in sorted(by_track.items()):
        print(f"  Track {track}: {'QUALIFIES' if all(outcomes) else 'DOES NOT QUALIFY'} "
              f"({sum(outcomes)}/{len(outcomes)} checks)")
    print(f"  {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed "
          f"in {time.perf_counter() - started:.1f}s")
    print("=" * 78)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
