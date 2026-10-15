"""FalkorDB connection layer.

Deliberately the only place in the codebase that knows how to reach the
database, so that local Docker and FalkorDB Cloud are interchangeable.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from falkordb import FalkorDB

from .config import Settings


class GraphStore:
    """Thin wrapper around a FalkorDB graph.

    Construction is deliberately exception-safe. The official client connects
    eagerly inside its own constructor, so building one against a database that
    is not running raises immediately — which means ``GraphStore(...)`` would
    blow up before any caller could report the problem, and a user who forgets
    ``docker compose up -d`` gets a traceback instead of instructions.

    The connection is therefore deferred, and any operation retries it once.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.from_env()
        self._client: FalkorDB | None = None
        self.graph = None
        self._connect()

    # ------------------------------------------------------------------ setup

    def client_kwargs(self) -> dict[str, Any]:
        """Connection kwargs, kept separate from construction.

        The official client connects eagerly inside its constructor, so the
        FalkorDB Cloud path (host/port/user/password/TLS) cannot otherwise be
        asserted without a live server.
        """
        s = self.settings
        kwargs: dict[str, Any] = {"host": s.host, "port": s.port}
        if s.username:
            kwargs["username"] = s.username
        if s.password:
            kwargs["password"] = s.password
        if s.tls:
            kwargs["ssl"] = True
        return kwargs

    def _build_client(self) -> FalkorDB:
        return FalkorDB(**self.client_kwargs())

    def _connect(self) -> bool:
        """Attempt (or re-attempt) the connection. Never raises."""
        try:
            self._client = self._build_client()
            self.graph = self._client.select_graph(self.settings.graph)
            return True
        except Exception:  # noqa: BLE001 - a down database is an expected state
            self._client = None
            self.graph = None
            return False

    def _graph(self):
        """The live graph handle, retrying the connection once if needed."""
        if self.graph is None and not self._connect():
            raise ConnectionError(
                f"FalkorDB is unreachable at {self.settings.host}:{self.settings.port}. "
                "Start it with: docker compose up -d"
            )
        return self.graph

    @property
    def connected(self) -> bool:
        return self.graph is not None

    # ----------------------------------------------------------------- querying

    def query(self, cypher: str, params: dict[str, Any] | None = None):
        """Run a read/write query."""
        return self._graph().query(cypher, params or {})

    def ro_query(self, cypher: str, params: dict[str, Any] | None = None):
        """Run a read-only query."""
        return self._graph().ro_query(cypher, params or {})

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
        """Delete the whole graph. Destructive — demo/dev only. Idempotent.

        Returns quietly if the database is unreachable, so callers that reset
        before checking connectivity do not have to guard the call.
        """
        if not self._connect():
            return
        try:
            self.graph.delete()
        except Exception:
            # The graph may not exist yet.
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
