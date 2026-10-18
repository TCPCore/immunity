"""Measure how the two speeds behave as the graph grows.

The fast path and the detection sweep are used differently, and the claim that
one is cheap enough to sit inline depends entirely on the other being the one
that grows. This measures both at increasing graph sizes so the claim is a
number rather than an assumption.

    python scripts/bench_scale.py
"""

from __future__ import annotations

import dataclasses
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from immunegraph.config import Settings  # noqa: E402
from immunegraph.db import GraphStore  # noqa: E402
from immunegraph.swarm import Swarm  # noqa: E402

SIZES = (100, 1_000, 5_000, 10_000)
KERNEL = (
    "The Enterprise plan costs 499 USD per seat per year",
    "The Team plan costs 99 USD per seat per year",
    "Refunds are available within 30 days of purchase",
    "Critical incidents are acknowledged within 15 minutes",
    "Customer data for EU customers is stored in the EU",
)
PROBE_CLAIM = "The Enterprise plan costs 399 USD per seat per year"


def build(store: GraphStore, total: int) -> None:
    """Insert claims in batches. One UNWIND per 500 keeps setup off the clock."""
    batch = 500
    for start in range(0, total, batch):
        rows = [
            {"id": f"bench_{i:06d}", "text": f"benchmark claim number {i} about plan {i % 97}"}
            for i in range(start, min(start + batch, total))
        ]
        store.query(
            """
            UNWIND $rows AS row
            CREATE (c:Claim {id: row.id, text: row.text, status: 'active'})
            """,
            {"rows": rows},
        )


def main() -> int:
    base = Settings.from_env()
    print("=" * 74)
    print(f"SCALE BENCHMARK  target {base.host}:{base.port}")
    print("=" * 74)
    print(f"  {'claims':>8}  {'fast path (ms)':>16}  {'sweep (ms)':>12}  {'kernel ms':>10}")
    print(f"  {'-'*8}  {'-'*16}  {'-'*12}  {'-'*10}")

    for size in SIZES:
        store = GraphStore(dataclasses.replace(base, graph=f"{base.graph}_bench_{size}"))
        store.reset()
        store.create_indexes()
        swarm = Swarm(store)
        swarm.start("bench", f"s_bench_{size}")
        for fact in KERNEL:
            swarm.kernel.register_fact(fact, trust_level="primary")

        build(store, size)
        facts = swarm.kernel.facts()

        # Fast path: judge one claim against a fixed-size kernel.
        samples = []
        for i in range(20):
            started = time.perf_counter()
            swarm.sanitizer.judge(f"probe_{i}", PROBE_CLAIM, facts)
            samples.append((time.perf_counter() - started) * 1000)

        # Detection sweep: the part that has to look at the whole graph.
        started = time.perf_counter()
        swarm.scanner.sweep()
        sweep_ms = (time.perf_counter() - started) * 1000

        print(f"  {size:>8,}  {statistics.median(samples):>16.2f}  "
              f"{sweep_ms:>12.1f}  {len(facts):>10}")
        store.graph.delete()

    print("=" * 74)
    print("  fast path is O(kernel); the sweep is O(graph). That is the")
    print("  argument for one inline and the other asynchronous.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
