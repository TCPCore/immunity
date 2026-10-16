"""Append-only write-ahead log — the durable half of the truth.

FalkorDB is an in-memory graph database. Locally it persists through a Docker
volume, but **FalkorDB Cloud's free tier explicitly has no persistence and no
backups**: instances are stopped after a day idle and deleted after seven. A
kernel of truth that evaporates is not much of a truth.

So the graph is treated as a *projection*: every write is appended here first,
and the graph can be rebuilt from the log at any time. This is the pattern
FalkorDB's own reference architecture recommends for agent memory — an event
store for durability, a graph for the live queryable view.

Why the log stores statements rather than semantic events:

    Semantic events ("ClaimAsserted {text: ...}") read better, but replay then
    has to reconstruct *which* write that implied, and any divergence between
    the event vocabulary and the writer's behaviour shows up as a graph that
    silently differs from the original. Statements cannot diverge: replaying
    them *is* the original write.

Ids are the reason this matters. Infection and antibody ids are `uuid4`, so a
replay that regenerated them would produce a different graph — every reference
broken. Because the log stores the fully-substituted statement, the generated id
is captured with it and replay is exact. Semantic labels are kept alongside for
the UI, so the log stays readable without becoming the source of truth.

Write-ahead ordering: the entry is appended *before* the statement executes, so
a crash mid-write leaves a log that is ahead of the graph rather than behind it.
Replay is tolerant of a failing statement and reports it, so one bad entry
cannot block a rebuild.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS writes (
    seq    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     TEXT NOT NULL,
    label  TEXT NOT NULL,
    cypher TEXT NOT NULL,
    params TEXT NOT NULL
);
"""


@dataclass
class LogEntry:
    seq: int
    ts: str
    label: str
    cypher: str
    params: dict

    def summary(self) -> str:
        return f"{self.label}: {self.cypher.strip().splitlines()[0][:72]}"


class WriteLog:
    """Append-only log of every write the application performs."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._lock = threading.Lock()
        # The concurrency tests write from several threads, so the connection is
        # shared and every access is serialised by the lock.
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------ write

    def record(self, label: str, cypher: str, params: dict | None = None) -> int:
        from .models import utcnow

        with self._lock:
            cursor = self._conn.execute(
                "INSERT INTO writes (ts, label, cypher, params) VALUES (?, ?, ?, ?)",
                (utcnow(), label, cypher, json.dumps(params or {}, default=str)),
            )
            self._conn.commit()
            return int(cursor.lastrowid or 0)

    # ------------------------------------------------------------------- read

    def entries(self, since: int = 0, limit: int | None = None) -> list[LogEntry]:
        sql = "SELECT seq, ts, label, cypher, params FROM writes WHERE seq > ? ORDER BY seq"
        if limit:
            sql += f" LIMIT {int(limit)}"
        with self._lock:
            rows = self._conn.execute(sql, (since,)).fetchall()
        return [
            LogEntry(seq=r[0], ts=r[1], label=r[2], cypher=r[3], params=json.loads(r[4]))
            for r in rows
        ]

    def __iter__(self):
        return iter(self.entries())

    def __len__(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM writes").fetchone()[0])

    def count(self) -> int:
        return len(self)

    def labels(self, limit: int = 200) -> list[dict]:
        """Recent entries with a short summary, for display."""
        return [
            {"seq": e.seq, "ts": e.ts, "label": e.label, "summary": e.summary()}
            for e in self.entries(limit=limit)
        ]

    def label_counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT label, COUNT(*) FROM writes GROUP BY label ORDER BY COUNT(*) DESC"
            ).fetchall()
        return {r[0]: int(r[1]) for r in rows}

    # ------------------------------------------------------------------ admin

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM writes")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
