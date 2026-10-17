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


STREAMLIT_SECRETS_BRIDGED = 0
STREAMLIT_SECRETS_ERROR: str | None = None
# Key *names* the app could see. Never values — this exists so a misconfigured
# deployment can be diagnosed from a screenshot without exposing a secret.
STREAMLIT_SECRETS_SEEN: tuple[str, ...] = ()


def _apply_secret(key: str, value) -> int:
    """Map one secret onto environment variables. Returns how many were set."""
    name = str(key).upper()
    if isinstance(value, (str, int, float, bool)):
        if name not in os.environ:
            os.environ[name] = str(value)
            return 1
        return 0
    # A TOML section such as [falkordb] with host/port/user flattens to
    # FALKORDB_HOST / FALKORDB_PORT / FALKORDB_USER. Both that and the flat
    # form are accepted, because "which shape does it want" is not a thing a
    # deployer should have to discover.
    if hasattr(value, "items"):
        count = 0
        for sub_key, sub_value in value.items():
            if isinstance(sub_value, (str, int, float, bool)):
                flat = f"{name}_{str(sub_key).upper()}"
                if flat not in os.environ:
                    os.environ[flat] = str(sub_value)
                    count += 1
        return count
    return 0


def _load_streamlit_secrets() -> int:
    """Expose Streamlit Cloud secrets as environment variables.

    Streamlit Community Cloud supplies configuration through ``st.secrets``,
    not environment variables. Without this bridge FALKORDB_HOST and its
    companions never arrive and the deployed app cannot connect.

    Only runs when Streamlit is already imported — which is true under
    ``streamlit run`` (app.py imports it before this module) and false for the
    CLI tools, so those avoid the import cost and the bare-mode warnings.
    ``setdefault`` semantics mean a real environment variable always wins.
    """
    global STREAMLIT_SECRETS_ERROR, STREAMLIT_SECRETS_SEEN
    if "streamlit" not in sys.modules:
        return 0
    try:
        import streamlit as st

        keys = [str(k) for k in st.secrets]
        STREAMLIT_SECRETS_SEEN = tuple(keys)
        bridged = 0
        for key in keys:
            bridged += _apply_secret(key, st.secrets[key])
        return bridged
    except Exception as exc:  # noqa: BLE001 - no secrets configured is normal
        # Recorded rather than swallowed. A malformed TOML block otherwise looks
        # exactly like "no secrets set", and the app then silently talks to
        # localhost — which is a genuinely confusing failure to debug remotely.
        STREAMLIT_SECRETS_ERROR = f"{type(exc).__name__}: {exc}"
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
