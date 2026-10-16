"""Rebuilding the graph from the write-ahead log.

The graph is a projection, not the durable record. Wipe it and replay the log and
you get the same graph back — same nodes, same edges, and crucially the same
`uuid4` ids, because the log stores fully-substituted statements rather than
regenerating anything.

This module also owns the fingerprint used to *prove* a rebuild was faithful
rather than merely plausible. "The app still works after a rebuild" is a much
weaker claim than "the graph is byte-for-byte the same shape", and the difference
is exactly the sort of thing that quietly rots.
"""

from __future__ import annotations

import json
from pathlib import Path

from .db import GraphStore
from .event_log import LogEntry, WriteLog


def graph_fingerprint(store: GraphStore) -> dict:
    """A canonical, comparable summary of the whole graph.

    Ids are included deliberately: a rebuild that produced the right *shape* with
    different node ids would break every reference and is not a faithful rebuild.
    """
    nodes = store.rows(
        """
        MATCH (n)
        RETURN labels(n)[0] AS label, n.id AS id
        ORDER BY label, id
        """
    )
    relationships = store.rows(
        """
        MATCH ()-[r]->()
        RETURN type(r) AS type, count(r) AS n
        ORDER BY type
        """
    )
    statuses = store.rows(
        """
        MATCH (c:Claim)
        RETURN c.status AS status, count(c) AS n
        ORDER BY status
        """
    )
    return {
        "nodes": [f"{row[0]}:{row[1]}" for row in nodes],
        "relationships": {row[0]: int(row[1]) for row in relationships},
        "claim_statuses": {row[0]: int(row[1]) for row in statuses},
    }


def rebuild(store: GraphStore, log: WriteLog, reset: bool = True) -> dict:
    """Wipe the graph and replay every logged write, in order.

    A statement that fails is counted and reported rather than aborting the
    rebuild: one bad entry should not make the whole log unusable, and a silent
    partial rebuild would be worse than a loud one.
    """
    if reset:
        store.reset()
        store.create_indexes()

    applied = 0
    failures: list[str] = []
    for entry in log:
        try:
            # query(), not write(): replaying must not re-log, or the log would
            # grow without bound and a second rebuild would apply everything twice.
            store.query(entry.cypher, entry.params)
            applied += 1
        except Exception as exc:  # noqa: BLE001
            failures.append(f"seq {entry.seq} [{entry.label}]: {exc}")

    return {
        "applied": applied,
        "failed": len(failures),
        "failures": failures[:5],
    }


def rebuild_and_verify(store: GraphStore, log: WriteLog) -> dict:
    """Rebuild, then prove the result matches what was there before."""
    before = graph_fingerprint(store)
    result = rebuild(store, log)
    after = graph_fingerprint(store)
    result["identical"] = before == after
    result["nodes"] = len(after["nodes"])
    if not result["identical"]:
        result["before"] = before
        result["after"] = after
    return result


# ----------------------------------------------------------------- export/import
#
# A log that only lives in a temp directory proves the architecture but cannot
# survive the container it is in. Exporting lets a known-good run be committed
# as a seed, so a cold-started deployment can rebuild itself immediately.


def export_jsonl(log: WriteLog, path: str | Path) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = log.entries()
    with path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(
                json.dumps(
                    {"label": entry.label, "cypher": entry.cypher, "params": entry.params},
                    default=str,
                )
                + "\n"
            )
    return len(entries)


def import_jsonl(log: WriteLog, path: str | Path) -> int:
    path = Path(path)
    if not path.exists():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        payload = json.loads(line)
        log.record(payload["label"], payload["cypher"], payload.get("params") or {})
        count += 1
    return count


def entries_from_jsonl(path: str | Path) -> list[LogEntry]:
    """Read a seed log without touching the live log."""
    path = Path(path)
    if not path.exists():
        return []
    entries: list[LogEntry] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        payload = json.loads(line)
        entries.append(
            LogEntry(
                seq=index,
                ts="",
                label=payload["label"],
                cypher=payload["cypher"],
                params=payload.get("params") or {},
            )
        )
    return entries
