"""Environment-driven configuration.

Nothing here may be hardcoded: the same code must serve a local Docker
container and a FalkorDB Cloud instance by changing .env alone.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the repository root if present.
REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")


def _load_streamlit_secrets() -> int:
    """Expose Streamlit Cloud secrets as environment variables.

    Streamlit Community Cloud supplies configuration through ``st.secrets``,
    not environment variables. Without this bridge FALKORDB_HOST and its
    companions never arrive and the deployed app cannot connect.

    Only runs when Streamlit is already imported — which is true under
    ``streamlit run`` (app.py imports it before this module) and false for the
    CLI tools, so those avoid the import cost and the bare-mode warnings.
    ``setdefault`` means a real environment variable always wins over a secret.
    """
    if "streamlit" not in sys.modules:
        return 0
    try:
        import streamlit as st

        bridged = 0
        for key in st.secrets:
            value = st.secrets[key]
            if isinstance(value, (str, int, float, bool)) and key not in os.environ:
                os.environ[key] = str(value)
                bridged += 1
        return bridged
    except Exception:  # noqa: BLE001 - no secrets configured is normal
        return 0


STREAMLIT_SECRETS_BRIDGED = _load_streamlit_secrets()


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
    write_log_path: str
    seed_log_path: str | None

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
            # Durable write-ahead log. FalkorDB Cloud's free tier has no
            # persistence, so the graph is treated as a rebuildable projection.
            write_log_path=(os.getenv("WRITE_LOG_PATH") or "/tmp/immunegraph-writes.db").strip(),
            seed_log_path=(os.getenv("SEED_LOG") or "").strip() or None,
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
