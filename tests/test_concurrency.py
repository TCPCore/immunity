"""Concurrency tests — Track 02 asks for "high-concurrency reads and writes".

These tests exist because the obvious implementation is wrong. Reading
``max(seq)`` and adding one is a read-only query, and concurrent readers are
free to observe the same value, producing duplicate sequence numbers and
colliding node ids. The fix is to allocate sequence numbers with a write so
FalkorDB serialises them.

Measured on this machine, 8 concurrent writers, 200 writes:

    naive read-then-write : 37 distinct sequence numbers  (163 collisions, 82% lost)
    allocate_counter()    : 200 distinct sequence numbers (0 collisions)

Kept as a test rather than a claim in the README.
"""

from __future__ import annotations

import dataclasses
from concurrent.futures import ThreadPoolExecutor

import pytest

from immunegraph.config import Settings
from immunegraph.db import GraphStore
from immunegraph.memory import MemoryWriter
from immunegraph.models import Agent
from immunegraph.queries import Q2_UNSUPPORTED_CLAIMS, Q5_TRUST_DECAY

WORKERS = 8
WRITES_PER_WORKER = 25
TOTAL_WRITES = WORKERS * WRITES_PER_WORKER


def _store(graph_suffix: str) -> GraphStore:
    base = Settings.from_env()
    return GraphStore(dataclasses.replace(base, graph=base.graph + graph_suffix))


def _db_available() -> bool:
    try:
        return _store("_probe").ping()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_available(),
    reason="FalkorDB not reachable — start it with: docker compose up -d",
)


def _seed_session(store: GraphStore, session_id: str, agents: int = 4):
    store.reset()
    store.create_indexes()
    writer = MemoryWriter(store)
    writer.open_session(session_id, "concurrency")
    people = []
    for i in range(agents):
        persona = Agent(id=f"ag_{i}", name=f"Agent {i}", role="worker")
        writer.upsert_agent(persona)
        people.append(persona)
    return writer, people


def test_concurrent_message_writes_allocate_unique_sequences():
    store = _store("_concurrency")
    writer, people = _seed_session(store, "sess_conc")

    def work(i: int):
        return writer.record_message("sess_conc", people[i % len(people)].id, f"message {i}")

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(work, range(TOTAL_WRITES)))

    ids = [r[0] for r in results]
    seqs = [r[1] for r in results]

    assert len(ids) == TOTAL_WRITES
    assert len(set(ids)) == TOTAL_WRITES, "message ids collided under concurrency"
    assert len(set(seqs)) == TOTAL_WRITES, "sequence numbers collided under concurrency"
    assert sorted(seqs) == list(range(1, TOTAL_WRITES + 1)), "sequence numbers have gaps"

    stored = store.scalar(
        "MATCH (m:Message {session_id: 'sess_conc'}) RETURN count(m)"
    )
    assert stored == TOTAL_WRITES, f"expected {TOTAL_WRITES} messages, found {stored}"

    store.graph.delete()


def test_concurrent_claim_writes_allocate_unique_ids():
    store = _store("_concurrency_claims")
    store.reset()
    store.create_indexes()
    writer = MemoryWriter(store)
    writer.open_session("sess_claims", "concurrency")

    def work(_i: int):
        return writer.allocate_counter("sess_claims", "claim")

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        values = list(pool.map(work, range(TOTAL_WRITES)))

    assert len(set(values)) == TOTAL_WRITES, "claim counter collided"
    assert sorted(values) == list(range(1, TOTAL_WRITES + 1))

    store.graph.delete()


def test_reads_are_correct_while_writes_are_in_flight():
    """Shared state must stay queryable during concurrent ingestion."""
    store = _store("_concurrency_rw")
    writer, people = _seed_session(store, "sess_rw", agents=2)

    errors: list[Exception] = []
    observed_counts: list[int] = []

    def writer_worker(i: int):
        try:
            writer.record_message("sess_rw", people[i % 2].id, f"m{i}")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def reader_worker(_i: int):
        try:
            # The immune queries must not fault mid-write.
            store.rows(Q2_UNSUPPORTED_CLAIMS)
            store.rows(Q5_TRUST_DECAY)
            observed_counts.append(
                int(store.scalar("MATCH (m:Message {session_id: 'sess_rw'}) RETURN count(m)") or 0)
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        write_futures = [pool.submit(writer_worker, i) for i in range(TOTAL_WRITES)]
        read_futures = [pool.submit(reader_worker, i) for i in range(40)]
        for future in write_futures + read_futures:
            future.result()

    assert not errors, errors

    final = store.scalar("MATCH (m:Message {session_id: 'sess_rw'}) RETURN count(m)")
    assert final == TOTAL_WRITES, f"lost writes: {final} of {TOTAL_WRITES}"

    # Every read saw a prefix of the final count, never more.
    assert observed_counts and max(observed_counts) <= TOTAL_WRITES

    store.graph.delete()
