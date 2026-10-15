"""The Mars mission demo, headless.

Runs the full arc — cascade, detection, immune response, trust decay, immunity
recall, effectiveness — against a live FalkorDB instance. This is the same code
path the Streamlit app drives, so the recorded demo shows real work.

    python -m immunegraph.demo
    python -m immunegraph.demo --pause 1.5      # slower, for screen recording
    python -m immunegraph.demo --keep           # don't wipe the graph first
"""

from __future__ import annotations

import argparse
import sys
import time

from .agents import MARS_CASCADE, MARS_REPEAT, MARS_TOPIC
from .config import Settings
from .db import GraphStore
from .swarm import Swarm

WIDTH = 76


def banner(title: str) -> None:
    print()
    print("=" * WIDTH)
    print(title)
    print("=" * WIDTH)


def rule() -> None:
    print("-" * WIDTH)


def build_store() -> GraphStore:
    store = GraphStore(Settings.from_env())
    if not store.ping():
        raise SystemExit(
            f"FalkorDB is unreachable at {store.settings.host}:{store.settings.port}.\n"
            "Start it with:  docker compose up -d"
        )
    return store


def run(reset: bool = True, pause: float = 0.0, verbose: bool = True) -> dict:
    store = build_store()
    if reset:
        store.reset()
    store.create_indexes()

    swarm = Swarm(store)
    swarm.start(MARS_TOPIC)

    summary: dict = {"cascade": [], "infections": [], "antibodies": [],
                     "trust": [], "recall": None, "effectiveness": [], "stats": {}}

    def say(line: str) -> None:
        if verbose:
            print(line)
        if pause:
            time.sleep(pause)

    # ---------------------------------------------------------------- ACT I
    banner("ACT I  —  A LIE ENTERS THE SWARM")
    say("Three agents plan a Mars mission. None of them is lying;")
    say("each reasons correctly from the premise it was handed.")
    rule()

    for persona, text, note in MARS_CASCADE:
        say("")
        say(f"  {persona.name:>10}: \"{text}\"")
        say(f"  {'':>10}  ({note})")
        turn = swarm.say(persona, text)
        detail = f"  {'':>10}  -> {len(turn.claim_ids)} claim(s)"
        if turn.source_names:
            detail += f", sources: {', '.join(turn.source_names)}"
        else:
            detail += ", no source cited"
        say(detail)
        for child, parent in turn.derivations:
            say(f"  {'':>10}  -> DERIVED_FROM {child} -> {parent}")
        if turn.new_infection_ids:
            say(f"  {'':>10}  !! INFECTION DETECTED: {', '.join(turn.new_infection_ids)}")
        summary["cascade"].append(
            {"agent": persona.name, "text": text, "claims": turn.claim_ids,
             "sources": turn.source_names, "derivations": turn.derivations,
             "new_infections": turn.new_infection_ids, "method": turn.method}
        )

    # --------------------------------------------------------------- ACT II
    banner("ACT II  —  WHAT THE GRAPH FOUND")
    say("Severity is not an opinion. It is graph reach:")
    say("  Q4 walks DERIVED_FROM back to the unsupported root.")
    rule()
    for inf in swarm.infections():
        say(f"  [{inf['type']}] severity {inf['severity']:.1f}"
            f"  spread to {inf['spread_count']} downstream claim(s)")
        say(f"     root: \"{inf['root_claim']}\"")
    summary["infections"] = swarm.infections()

    # -------------------------------------------------------------- ACT III
    banner("ACT III  —  THE IMMUNE RESPONSE")
    say("An antibody is dispatched. It must cite a source, and the")
    say("correction enters the graph as a first-class Claim.")
    rule()
    for res in swarm.dispatch_antibodies():
        for line in res.lines():
            say(line)
        summary["antibodies"].append(
            {"antibody_id": res.antibody_id, "strategy": res.strategy,
             "correction": res.correction_text, "source": res.source_name,
             "severity_before": res.severity_before}
        )

    # --------------------------------------------------------------- ACT IV
    banner("ACT IV  —  TRUST DECAYS, DERIVED FROM THE GRAPH")
    say("No counter is decremented anywhere. Q5 recomputes trust from")
    say("exposure: asserting an infected claim, or deriving from one.")
    rule()
    say(f"  {'agent':<16}{'base':>6}{'infections':>12}{'effective':>11}")
    for row in swarm.trust_table():
        say(f"  {row['agent']:<16}{row['base_trust']:>6.2f}"
            f"{row['active_infections']:>12}{row['effective_trust']:>11.2f}")
    summary["trust"] = swarm.trust_table()

    # ---------------------------------------------------------------- ACT V
    banner("ACT V  —  IMMUNITY. THE SWARM DOES NOT GET FOOLED TWICE.")
    persona, text, note = MARS_REPEAT
    say(f"  {persona.name:>10}: \"{text}\"")
    say(f"  {'':>10}  ({note})")
    rule()

    recall = swarm.recall(text)
    say(f"  recall: {recall.describe()}")
    summary["recall"] = {
        "hit": recall.hit, "method": recall.method, "past_text": recall.past_text,
        "infection_type": recall.infection_type, "score": recall.score,
        "first_seen": recall.first_seen,
    }

    if recall.hit:
        turn = swarm.say(persona, text, sweep=False)
        infection = swarm.contain_repeat(turn.claim_ids[0], recall)
        say("  ACTION: claim quarantined on sight.")
        say(f"          NO cascade re-detection was required.")
        say(f"          repeat recorded as {infection.id if infection else 'n/a'}")
        say("          the earlier antibody is marked 'effective'")
        summary["contained"] = infection.id if infection else None

    # --------------------------------------------------------------- ACT VI
    banner("ACT VI  —  DID THE IMMUNE SYSTEM ACTUALLY WORK?")
    say("Q8 turns \"the swarm develops immunity\" into a measurement.")
    rule()
    say(f"  {'infection type':<22}{'outbreaks':>10}{'contained':>11}{'recurred':>10}")
    for row in swarm.effectiveness():
        say(f"  {row['infection_type']:<22}{row['outbreaks']:>10}"
            f"{row['contained']:>11}{row['recurred']:>10}")
    summary["effectiveness"] = swarm.effectiveness()

    # -------------------------------------------------------------- EPILOGUE
    banner("GRAPH STATE")
    for label, count in swarm.stats().items():
        say(f"  {label:<12}{count:>4}")
    summary["stats"] = swarm.stats()

    say("")
    say("  FalkorDB was not a place to store the answer. It was the")
    say("  thing that worked out the answer.")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="ImmuneGraph Mars mission demo")
    parser.add_argument("--pause", type=float, default=0.0,
                        help="seconds to pause between beats (use ~1.5 when recording)")
    parser.add_argument("--keep", action="store_true",
                        help="do not wipe the graph before running")
    parser.add_argument("--quiet", action="store_true", help="suppress the narrative")
    args = parser.parse_args()

    summary = run(reset=not args.keep, pause=args.pause, verbose=not args.quiet)

    failures = []
    if not summary["recall"] or not summary["recall"]["hit"]:
        failures.append("immunity recall did not fire")
    if not summary["infections"]:
        failures.append("no infection was detected")
    if not summary["antibodies"]:
        failures.append("no antibody responded")
    if failures:
        print("\nDEMO FAILED: " + "; ".join(failures))
        sys.exit(1)


if __name__ == "__main__":
    main()
