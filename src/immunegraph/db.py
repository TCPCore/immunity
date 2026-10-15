"""FalkorDB connection layer.

Deliberately the only place in the codebase that knows how to reach the
database, so that local Docker and FalkorDB Cloud are interchangeable.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from falkordb import FalkorDB

from .config import Settings


class GraphStore:
    """Thin wrapper around a FalkorDB graph."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.from_env()
        self._client = self._build_client()
        self.graph = self._client.select_graph(self.settings.graph)

    # ------------------------------------------------------------------ setup

    def _build_client(self) -> FalkorDB:
        s = self.settings
        kwargs: dict[str, Any] = {"host": s.host, "port": s.port}
        if s.username:
            kwargs["username"] = s.username
        if s.password:
            kwargs["password"] = s.password
        if s.tls:
            kwargs["ssl"] = True
        return FalkorDB(**kwargs)

    # ----------------------------------------------------------------- querying

    def query(self, cypher: str, params: dict[str, Any] | None = None):
        """Run a read/write query."""
        return self.graph.query(cypher, params or {})

    def ro_query(self, cypher: str, params: dict[str, Any] | None = None):
        """Run a read-only query."""
        return self.graph.ro_query(cypher, params or {})

    def rows(self, cypher: str, params: dict[str, Any] | None = None) -> list[list[Any]]:
        """Run a query and return just the result rows."""
        return self.query(cypher, params).result_set

    def columns(self, cypher: str, params: dict[str, Any] | None = None) -> list[str]:
        """Run a query and return its column headers."""
        return list(self.query(cypher, params).header)

    def dicts(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run a query and return rows as dicts keyed by column name."""
        result = self.query(cypher, params)
        header = list(result.header)
        return [dict(zip(header, row)) for row in result.result_set]

    def one(self, cypher: str, params: dict[str, Any] | None = None):
        """Return the first row, or None."""
        rows = self.rows(cypher, params)
        return rows[0] if rows else None

    def scalar(self, cypher: str, params: dict[str, Any] | None = None):
        """Return the first column of the first row, or None."""
        row = self.one(cypher, params)
        return row[0] if row else None

    # ------------------------------------------------------------------- admin

    def ping(self) -> bool:
        """True if the database answers."""
        try:
            self.query("RETURN 1")
            return True
        except Exception:
            return False

    def reset(self) -> None:
        """Delete the whole graph. Destructive — demo/dev only."""
        try:
            self.graph.delete()
        except Exception:
            # Graph may not exist yet.
            pass
        self.graph = self._client.select_graph(self.settings.graph)

    def run_many(self, statements: Iterable[str]) -> None:
        """Execute a sequence of statements, ignoring empty ones."""
        for statement in statements:
            if statement and statement.strip():
                self.query(statement)

    # ------------------------------------------------------------------ schema

    def create_indexes(self) -> list[str]:
        """Create the indexes from docs/GRAPH_MODEL.md section 4.

        FalkorDB raises if an index already exists, so each statement is
        attempted independently and failures are collected rather than
        aborting the whole bootstrap. Returns the statements that failed.
        """
        from .schema import INDEX_STATEMENTS

        failures: list[str] = []
        for statement in INDEX_STATEMENTS:
            try:
                self.query(statement)
            except Exception as exc:  # noqa: BLE001
                message = str(exc).lower()
                if "already" in message or "exists" in message:
                    continue
                failures.append(f"{statement} -> {exc}")
        return failures

    def node_count(self) -> int:
        return int(self.scalar("MATCH (n) RETURN count(n)") or 0)

    def labels(self) -> Sequence[str]:
        rows = self.rows("CALL db.labels()")
        return [r[0] for r in rows]

    def relationship_types(self) -> Sequence[str]:
        rows = self.rows("CALL db.relationshipTypes()")
        return [r[0] for r in rows]
