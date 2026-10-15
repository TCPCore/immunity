"""Configuration tests.

Two things are checked here that nothing else covers:

1. The FalkorDB Cloud path. README "Option B" tells a judge they can point the
   project at a hosted instance by setting four environment variables and
   changing no code. That claim was previously untested, because the official
   client connects eagerly and would need a real server. Separating
   ``client_kwargs`` from construction makes the mapping assertable.

2. `.env.example` completeness. If the code reads a variable the template does
   not document, a judge following the README silently gets a default instead.
   These tests are pure configuration and need no database.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from immunegraph.config import Settings
from immunegraph.db import GraphStore

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPO_ROOT / "src" / "immunegraph"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

CONFIG_VARS = [
    "FALKORDB_HOST",
    "FALKORDB_PORT",
    "FALKORDB_USER",
    "FALKORDB_PASSWORD",
    "FALKORDB_TLS",
    "FALKORDB_GRAPH",
    "EXTRACTOR",
    "TENANT_ID",
]


@pytest.fixture
def clean_env(monkeypatch):
    """Remove every variable the project reads, so defaults are exercised."""
    for name in CONFIG_VARS + ["OPENAI_API_KEY", "OPENAI_BASE_URL", "LLM_MODEL"]:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


# ------------------------------------------------------------------- defaults


def test_defaults_match_the_documented_docker_setup(clean_env):
    """The README says Docker needs no .env at all. That must stay true."""
    settings = Settings.from_env()
    assert settings.host == "localhost"
    assert settings.port == 6379
    assert settings.username is None
    assert settings.password is None
    assert settings.tls is False
    assert settings.graph == "immunegraph"
    assert settings.extractor == "auto"


def test_stored_credentials_are_not_described(clean_env):
    clean_env.setenv("FALKORDB_PASSWORD", "hunter2")
    described = Settings.from_env().describe()
    assert "hunter2" not in described
    assert "auth=yes" in described


# --------------------------------------------------------------- cloud option


def test_cloud_env_vars_reach_the_client(clean_env):
    """README Option B: four env vars, no code change."""
    clean_env.setenv("FALKORDB_HOST", "cloud.falkordb.example")
    clean_env.setenv("FALKORDB_PORT", "12345")
    clean_env.setenv("FALKORDB_USER", "falkordb")
    clean_env.setenv("FALKORDB_PASSWORD", "s3cret")
    clean_env.setenv("FALKORDB_TLS", "false")

    settings = Settings.from_env()
    assert settings.is_cloud, "a non-local host should be detected as cloud"
    # Port 9 keeps the eager client constructor from waiting on DNS.
    store = GraphStore(settings)
    kwargs = store.client_kwargs()

    assert kwargs["host"] == "cloud.falkordb.example"
    assert kwargs["port"] == 12345
    assert kwargs["username"] == "falkordb"
    assert kwargs["password"] == "s3cret"
    assert "ssl" not in kwargs, "free-tier Cloud has no TLS"


def test_tls_flag_adds_ssl(clean_env):
    clean_env.setenv("FALKORDB_HOST", "127.0.0.1")
    clean_env.setenv("FALKORDB_PORT", "9")
    clean_env.setenv("FALKORDB_TLS", "true")
    assert GraphStore(Settings.from_env()).client_kwargs()["ssl"] is True


def test_unauthenticated_local_omits_credentials(clean_env):
    clean_env.setenv("FALKORDB_HOST", "127.0.0.1")
    clean_env.setenv("FALKORDB_PORT", "9")
    kwargs = GraphStore(Settings.from_env()).client_kwargs()
    assert set(kwargs) == {"host", "port"}


def test_local_hosts_are_not_treated_as_cloud(clean_env):
    for host in ("localhost", "127.0.0.1", "::1", "falkordb"):
        clean_env.setenv("FALKORDB_HOST", host)
        assert not Settings.from_env().is_cloud, host


# ------------------------------------------------------------------ tenancy


def test_tenant_namespaces_the_graph(clean_env):
    """Track 02 asks for separate memory per tenant; one graph per tenant."""
    clean_env.setenv("FALKORDB_GRAPH", "immunegraph")
    clean_env.setenv("TENANT_ID", "acme")
    assert Settings.from_env().graph == "immunegraph_acme"


def test_default_tenant_does_not_rename_the_graph(clean_env):
    clean_env.setenv("TENANT_ID", "default")
    assert Settings.from_env().graph == "immunegraph"


def test_bad_port_falls_back_rather_than_crashing(clean_env):
    clean_env.setenv("FALKORDB_PORT", "not-a-number")
    assert Settings.from_env().port == 6379


# ------------------------------------------------------- template completeness


def _variables_read_by_code() -> set[str]:
    pattern_getenv = re.compile(r'os\.getenv\("([A-Z_]+)"')
    pattern_helper = re.compile(r'_(?:bool|int)\("([A-Z_]+)"')
    found: set[str] = set()
    for path in SOURCE_DIR.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        found |= set(pattern_getenv.findall(text))
        found |= set(pattern_helper.findall(text))
    return found


def test_env_example_documents_every_variable_the_code_reads():
    """Undocumented variables default silently, which is how judges get stuck."""
    documented = set(re.findall(r"^([A-Z_]+)=", ENV_EXAMPLE.read_text(encoding="utf-8"), re.M))
    missing = sorted(_variables_read_by_code() - documented)
    assert not missing, f"code reads these but .env.example omits them: {missing}"


def test_env_example_does_not_invent_variables():
    """A template listing variables nothing reads is equally misleading."""
    documented = set(re.findall(r"^([A-Z_]+)=", ENV_EXAMPLE.read_text(encoding="utf-8"), re.M))
    unknown = sorted(documented - _variables_read_by_code())
    assert not unknown, f".env.example documents unused variables: {unknown}"


def test_env_example_contains_no_real_secrets():
    """It is committed; only empty values or obvious placeholders are allowed."""
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if "KEY" in name or "PASSWORD" in name:
            assert value.strip() == "", f"{name} must not ship with a value"
