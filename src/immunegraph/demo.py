"""The Mars mission demo, headless.

Runs the full arc, cascade, detection, immune response, trust decay, immunity
recall, effectiveness, against a live FalkorDB instance. This is the same code
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
from .event_log import WriteLog
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


def run(reset: bool = True, pause: float = 0.0, verbose: bool = True,
        export_seed: str | None = None) -> dict:
    store = build_store()
    settings = Settings.from_env()
    log = WriteLog(settings.write_log_path)
    swarm = Swarm(store, log=log)
    if reset:
        # Clears the log as well as the graph, so a re-run does not replay a
        # previous run's writes on top of this one.
        swarm.reset_all()
    store.create_indexes()
    swarm.start(MARS_TOPIC)

    summary: dict = {"cascade": [], "infections": [], "antibodies": [],
                     "trust": [], "recall": None, "effectiveness": [], "stats": {}}

    def say(line: str) -> None:
        if verbose:
            print(line)
        if pause:
            time.sleep(pause)

    # ---------------------------------------------------------------- ACT I
    banner("ACT I ,  A LIE ENTERS THE SWARM")
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
    banner("ACT II ,  WHAT THE GRAPH FOUND")
    say("Severity is not an opinion. It is graph reach:")
    say("  Q4 walks DERIVED_FROM back to the unsupported root.")
    rule()
    for inf in swarm.infections():
        say(f"  [{inf['type']}] severity {inf['severity']:.1f}"
            f"  spread to {inf['spread_count']} downstream claim(s)")
        say(f"     root: \"{inf['root_claim']}\"")
    summary["infections"] = swarm.infections()

    # -------------------------------------------------------------- ACT III
    banner("ACT III ,  THE IMMUNE RESPONSE")
    say("An antibody is dispatched. It must cite a source, and the")
    say("correction enters the graph as a first-class Claim.")
    rule()
    for res in swarm.dispatch_antibodies():
        for line in res.lines():
            say(line)
        summary["antibodies"].append(
            {"antibody_id": res.antibody_id, "strategy": res.strategy,
             "correction": res.correction_text, "source": res.source_name,
             "severity_before": res.severity_before,
             "cascade_contained": res.cascade_contained}
        )

    # ------------------------------------------------------------- ACT III.b
    banner("ACT III.b ,  THE KERNEL OF TRUTH")
    say("Until now the swarm only remembered what was WRONG. A sourced")
    say("correction also becomes a fact, so the antibody teaches, not just")
    say("punishes, and every future claim is judged against it.")
    rule()
    stats = swarm.kernel_stats()
    say(f"  facts held        : {stats['facts']}")
    say(f"  claims aligned    : {stats['aligned_edges']}")
    say(f"  claims in conflict: {stats['conflict_edges']}")
    say("")
    for fact in swarm.kernel_facts():
        say(f"  [{fact['trust_level']}] {fact['text']!r}")
        prov = swarm.fact_provenance(fact["id"]) or {}
        say(f"      established by {prov.get('established_by') or '-'} "
            f"citing {prov.get('source') or '-'}")
    summary["kernel"] = swarm.kernel_stats()
    summary["kernel_facts"] = swarm.kernel_facts()

    # --------------------------------------------------------------- ACT IV
    banner("ACT IV ,  TRUST DECAYS, DERIVED FROM THE GRAPH")
    say("No counter is decremented anywhere. Q5 recomputes trust from")
    say("exposure: asserting an infected claim, or deriving from one.")
    rule()
    say(f"  {'agent':<16}{'base':>6}{'infections':>12}{'effective':>11}")
    for row in swarm.trust_table():
        say(f"  {row['agent']:<16}{row['base_trust']:>6.2f}"
            f"{row['active_infections']:>12}{row['effective_trust']:>11.2f}")
    summary["trust"] = swarm.trust_table()

    # ---------------------------------------------------------------- ACT V
    banner("ACT V ,  IMMUNITY. THE SWARM DOES NOT GET FOOLED TWICE.")
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

        # The fast path, and the point of the kernel: the sanitiser recognises
        # this claim against a fact, without an extraction call and without
        # re-running any part of the cascade detection.
        for verdict in turn.verdicts:
            if verdict["verdict"] == "conflicts":
                say(f"  SANITISER : contradicts kernel fact {verdict['fact']!r}")
                say(f"              overlap {verdict['overlap']:.2f}, "
                    f"decided in {verdict['elapsed_ms']:.1f}ms, no model call, no cascade scan")
            else:
                say(f"  SANITISER : {verdict['verdict']} ({verdict['elapsed_ms']:.1f}ms)")

        infection = swarm.contain_repeat(turn.claim_ids[0], recall)
        say("  ACTION: claim quarantined on sight.")
        say("          NO cascade re-detection was required.")
        say(f"          repeat recorded as {infection.id if infection else 'n/a'}")
        say("          the earlier antibody is marked 'effective'")
        summary["contained"] = infection.id if infection else None
        summary["verdicts"] = turn.verdicts

    # --------------------------------------------------------------- ACT VI
    banner("ACT VI ,  DID THE IMMUNE SYSTEM ACTUALLY WORK?")
    say("Q8 turns \"the swarm develops immunity\" into a measurement.")
    rule()
    say(f"  {'infection type':<22}{'outbreaks':>10}{'contained':>11}{'recurred':>10}")
    for row in swarm.effectiveness():
        say(f"  {row['infection_type']:<22}{row['outbreaks']:>10}"
            f"{row['contained']:>11}{row['recurred']:>10}")
    summary["effectiveness"] = swarm.effectiveness()

    # -------------------------------------------------------------- EPILOGUE
    banner("ACT VII ,  THE SWARM IS HARDER TO FOOL THAN IT WAS")
    rule()
    stats = swarm.kernel_stats()
    summary_stats = swarm.sanitizer_summary()
    say(f"  kernel facts held     : {stats['facts']}")
    say(f"  claims in conflict    : {stats['conflict_edges']}")
    say(f"  fast-path recognitions: {summary_stats['fast_path_hits']} "
        f"(avg {summary_stats['avg_ms']}ms, no model call)")
    say("")
    say("  Detection finds lies the swarm has never seen, by walking the")
    say("  support structure. The kernel recognises lies it already knows,")
    say("  by comparison. One gets more expensive as the graph grows; the")
    say("  other does not, that is what immunity means here.")

    # -------------------------------------------------------------- ACT VIII
    banner("ACT VIII ,  DURABILITY: THE GRAPH IS A PROJECTION")
    say("FalkorDB Cloud's free tier has no persistence and no backups. So")
    say("every write is appended to a log BEFORE it is applied, and the graph")
    say("can be rebuilt from that log at any time.")
    rule()
    say(f"  writes logged : {swarm.log_count()}")
    top = list(swarm.log_label_counts().items())[:4]
    say(f"  most frequent : " + ", ".join(f"{label} x{n}" for label, n in top))
    result = swarm.rebuild_from_log()
    say("")
    say(f"  graph wiped and rebuilt from the log")
    say(f"  replayed      : {result['applied']} writes, {result['failed']} failed")
    say(f"  identical     : {result['identical']}")
    say("")
    say("  Identical includes node ids. That is why the log stores")
    say("  statements rather than semantic events: replaying a statement")
    say("  reproduces the generated id exactly, whereas regenerating one")
    say("  would produce a different graph with every reference broken.")
    summary["durability"] = {
        "writes": swarm.log_count(),
        "applied": result["applied"],
        "failed": result["failed"],
        "identical": result["identical"],
    }

    if export_seed:
        from .projection import export_jsonl

        written = export_jsonl(log, export_seed)
        say("")
        say(f"  seed log exported: {written} writes -> {export_seed}")
        say("  Set SEED_LOG to this path and a cold-started deployment")
        say("  rebuilds the whole story without anyone clicking anything.")
        summary["durability"]["seed"] = export_seed

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
    parser.add_argument(
        "--export-seed",
        metavar="PATH",
        help="also write this run's write-ahead log to PATH as JSONL, for SEED_LOG",
    )
    args = parser.parse_args()

    summary = run(
        reset=not args.keep,
        pause=args.pause,
        verbose=not args.quiet,
        export_seed=args.export_seed,
    )

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
