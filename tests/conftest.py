"""Pin the test suite to the local Docker instance.

A developer's `.env` may legitimately point at FalkorDB Cloud, because that is
what the deployed app uses. Tests, however, create and delete graphs, so running
them against a cloud instance would pollute real data and make every assertion
slow and network-dependent.

Setting these before ``immunegraph.config`` is imported is sufficient:
``load_dotenv()`` does not override variables that already exist, so these win
over `.env`.
"""

from __future__ import annotations

import os

os.environ["FALKORDB_HOST"] = "localhost"
os.environ["FALKORDB_PORT"] = "6379"
os.environ["FALKORDB_USER"] = ""
os.environ["FALKORDB_PASSWORD"] = ""
os.environ["FALKORDB_TLS"] = "false"
os.environ["SEED_LOG"] = ""
os.environ.setdefault("WRITE_LOG_PATH", "/tmp/immunegraph-test-writes.db")
