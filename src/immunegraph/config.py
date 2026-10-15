"""Environment-driven configuration.

Nothing here may be hardcoded: the same code must serve a local Docker
container and a FalkorDB Cloud instance by changing .env alone.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the repository root if present.
REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Resolved runtime settings."""

    host: str
    port: int
    username: str | None
    password: str | None
    tls: bool
    graph: str
    extractor: str
    tenant_id: str

    @classmethod
    def from_env(cls) -> "Settings":
        username = (os.getenv("FALKORDB_USER") or "").strip() or None
        password = (os.getenv("FALKORDB_PASSWORD") or "").strip() or None
        graph = (os.getenv("FALKORDB_GRAPH") or "immunegraph").strip()

        # FalkorDB Cloud namespaces graphs per instance; local Docker does not.
        tenant = (os.getenv("TENANT_ID") or "").strip()
        if tenant and tenant != "default":
            graph = f"{graph}_{tenant}"

        return cls(
            host=(os.getenv("FALKORDB_HOST") or "localhost").strip(),
            port=_int("FALKORDB_PORT", 6379),
            username=username,
            password=password,
            tls=_bool("FALKORDB_TLS", False),
            graph=graph,
            extractor=(os.getenv("EXTRACTOR") or "auto").strip().lower(),
            tenant_id=tenant or "default",
        )

    @property
    def is_cloud(self) -> bool:
        return self.host not in {"localhost", "127.0.0.1", "falkordb", "::1"}

    def describe(self) -> str:
        """Human-readable connection summary with no secrets in it."""
        # Auth is configured if either credential is present: Redis accepts a
        # password on its own, so keying off the username alone under-reports.
        auth = "yes" if (self.username or self.password) else "no"
        return (
            f"{self.host}:{self.port} graph={self.graph!r} "
            f"tls={self.tls} auth={auth} extractor={self.extractor}"
        )
