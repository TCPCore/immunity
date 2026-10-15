# ImmuneGraph — a self-healing memory for AI agent swarms

**A FalkorDB-backed immune system that catches hallucination cascades in multi-agent
systems, corrects them, and remembers them so the swarm develops immunity.**

Built for **Graph Hacks: Context for AI Agents** (WeMakeDevs × FalkorDB, Oct 15–18 2026).

> **Tracks entered: 01 — Agents That Act on Connected Data, and 02 — Agent Memory and
> Coordination.**

---

## The problem

Multi-agent AI systems have a failure mode that ordinary memory layers do not address.
One agent invents a claim, a second derives a plan from it, a third builds on that plan —
and the swarm calmly acts on a lie. Existing memory stores *what was said*. None of them
notice that the swarm is getting **epistemically sick**.

## The idea

Treat every claim, source, agent and derivation as a graph node. Then the pathologies
become traversals:

| Pathology | Graph shape |
| --- | --- |
| Circular support | a cycle in `SUPPORTED_BY` / `DERIVED_FROM` |
| Unsupported claim | a claim with no `Source` and no parent |
| Contradiction | a `CONTRADICTS` edge between two asserted claims |
| Cascade | reachability along `DERIVED_FROM` back to a bad root |
| Failed memory | association with an unresolved `Infection` node |

When a query finds an infection, ImmuneGraph creates an `Infection` node whose **severity
is computed from graph reachability** — not from an LLM's opinion — then dispatches an
**antibody agent** that injects a sourced correction, quarantines the claim, and records the
episode. When a similar claim appears later, immunity recall fires **instantly**, without
re-running cascade detection.

**The graph is the immune system.** Remove FalkorDB and there is no product: detection,
severity, trust and recall are all Cypher queries. See
[`docs/GRAPH_MODEL.md`](docs/GRAPH_MODEL.md).

---

## Graph model

Nodes: `Tenant` · `Session` · `Message` · `Agent` · `Claim` · `Source` · `Infection` · `Antibody`

| Memory type | Nodes | Role |
| --- | --- | --- |
| Episodic | `Message`, `Session` | append-only conversation history |
| Semantic | `Claim`, `Source`, `Agent` | durable beliefs and who holds them |
| Procedural | `Antibody`, `Infection` | how the swarm responds to a class of failure |

Relationships: `SPOKE` · `MENTIONS` · `ASSERTS` · `SUPPORTED_BY` · `DERIVED_FROM` ·
`CONTRADICTS` · `REPEATS` · `TRUSTS` · `HANDOFF_TO` · `TARGETS` · `SPREAD_TO` ·
`NEUTRALIZES` · `INJECTS`

The full schema, indexes, and the eight load-bearing Cypher queries are in
**[`docs/GRAPH_MODEL.md`](docs/GRAPH_MODEL.md)**. Those queries are the product.

---

## Setup

### Option A — Docker (recommended)

```bash
docker compose up -d          # FalkorDB on localhost:6379
cp .env.example .env          # then fill in OPENAI_API_KEY
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m immunegraph.verify_queries   # proves every Cypher query runs
.venv/bin/streamlit run app.py
```

### Option B — FalkorDB Cloud

Create a free instance at [app.falkordb.cloud](https://app.falkordb.cloud), then set
`FALKORDB_HOST`, `FALKORDB_PORT`, `FALKORDB_USER`, `FALKORDB_PASSWORD` in `.env`.
No other change is needed — the connection layer is environment-driven.

> **Platform note.** `falkordblite` (embedded) is *not* used. Its published macOS wheels are
> tagged universal2 but contain **arm64-only** `redis-server` and `falkordb.so`, so it fails
> on x86_64 Macs with `[Errno 86] Bad CPU type in executable`. This is an upstream packaging
> bug, not a missing dependency. Use the container or Cloud.

### Run the demo headless

```bash
.venv/bin/python -m immunegraph.demo
```

---

## How it works, in one pass

1. **Researcher** asserts *"Mars has breathable oxygen at night."*
2. **Q2** finds a claim with no trusted source → `Infection` created.
3. **Analyst** derives *"If Mars has breathable oxygen, we don't need helmets."*
4. **Planner** derives *"We can skip life support."*
5. **Q4** walks the cascade, computes reach and severity from the graph.
6. **Antibody** injects *"NASA: Mars' atmosphere is ~95% CO₂, no breathable oxygen."*
   The root claim is quarantined and the false branch is neutralised.
7. **Q5** shows Researcher's trust decaying as a *derived* value.
8. Researcher repeats *"Mars has oxygen at night."* → **Q6/Q7 immunity recall fires
   instantly.** The swarm does not get fooled twice.
9. **Q8** measures whether antibodies actually worked across outbreaks.

---

## Verification

A Cypher query that has never been executed is not a feature. `verify_queries.py` executes
every query in `docs/GRAPH_MODEL.md` against a live instance and exits non-zero on failure.

```bash
.venv/bin/python -m immunegraph.verify_queries
.venv/bin/python -m pytest
```

FalkorDB implements a **subset** of openCypher v9. The dialect constraints that shaped this
model — no regex operator, no label expressions, no aggregation inside pattern
comprehensions, `LIMIT` not constraining eager writes — are documented in
`docs/GRAPH_MODEL.md` §6.

---

## AI coding assistant disclosure

Required by hackathon rule 11. **Edit this section to be accurate before submitting.**

- AI coding assistants were used for: scaffolding, boilerplate, documentation drafting, and
  Cypher dialect research.
- All graph model decisions, the immune-system design, and the final queries were reviewed,
  executed, and validated against a live FalkorDB instance by the team.
- Every Cypher query in this repository has been run and its results inspected.
- The demo video shows a real run against a real FalkorDB instance.

## Data

The Mars mission scenario is **synthetic**, written for this demo. No private, personal, or
paywalled data is used. The only external factual claim is the composition of the Martian
atmosphere (~95% CO₂), which is public NASA science.

## Team

- cptlight

## License

MIT
