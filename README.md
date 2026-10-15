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
**[`docs/GRAPH_MODEL.md`](docs/GRAPH_MODEL.md)**. Those queries are the product; this
README repeats the four headline ones so a reviewer can see them without leaving the page.

**Q1 — self-justifying belief (a cycle in the support structure)**

```cypher
MATCH path = (c:Claim)-[:SUPPORTED_BY|DERIVED_FROM*2..5]->(c)
RETURN c.id, c.text, [n IN nodes(path) | n.text] AS cycle_path, length(path)
```

**Q2 — an unsupported root (no trusted source, no parent)**

```cypher
MATCH (c:Claim {status: 'active'})
OPTIONAL MATCH (c)-[:SUPPORTED_BY]->(s:Source) WHERE s.trust_level <> 'unreliable'
WITH c, count(s) AS source_count
OPTIONAL MATCH (c)-[:DERIVED_FROM]->(p:Claim)
WITH c, source_count, count(p) AS parent_count
WHERE source_count = 0 AND parent_count = 0
RETURN c.id, c.text, c.fingerprint
```

**Q4 — cascade reach. Severity is defined as this traversal, not as an LLM's opinion**

```cypher
MATCH (root:Claim {id: $root_claim_id})
OPTIONAL MATCH (down:Claim)-[:DERIVED_FROM*1..5]->(root)
WITH root, collect(DISTINCT down) AS downs
WITH root, downs, downs + [root] AS members
UNWIND members AS member
OPTIONAL MATCH (a:Agent)-[:ASSERTS]->(member)
RETURN root.id, size(downs) AS infected_claims, count(DISTINCT a) AS exposed_agents
```

**Q6 — immunity recall. An indexed equality match, so it is instant, not a model judgement**

```cypher
MATCH (past:Claim {fingerprint: $fingerprint})<-[:TARGETS]-(i:Infection)
RETURN past.id, past.text, past.status, i.id, i.type, i.detected_at, i.resolved_at
```

Q3 (contradiction clusters), Q5 (derived trust decay), Q7 (vector recall of paraphrases) and
Q8 (antibody effectiveness) are in [`docs/GRAPH_MODEL.md`](docs/GRAPH_MODEL.md) §5 and
[`src/immunegraph/queries.py`](src/immunegraph/queries.py).

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

Required by hackathon rule 11. Being specific rather than vague about it:

- **AI-assisted:** the FalkorDB connection and schema modules, the query registry and its
  docstrings, the live query verifier, the extraction module, documentation drafting, and
  research into FalkorDB's openCypher subset and vector-index API.
- **Human-authored:** the project concept and the immune-system framing, the choice of
  Tracks 01 and 02, the Mars mission scenario and its beats, the memory taxonomy mapping onto
  the Track 02 rubric, and the decision to run FalkorDB in Docker.
- **Verification — the part that matters most:** every Cypher query in this repository was
  executed against a live FalkorDB instance before being committed, via
  `python -m immunegraph.verify_queries`. Two non-obvious behaviours were discovered by
  *running* the queries rather than assuming them; one of them (the vector recall ordering
  bug) is a silent correctness bug that the verifier does **not** catch. Both are written up
  in [`docs/EXPLAINER.md`](docs/EXPLAINER.md) §4.
- The team can explain the graph model, each detection query, and the reasoning behind the
  memory taxonomy. [`docs/EXPLAINER.md`](docs/EXPLAINER.md) is the written form of that
  explanation.

> **Before submitting:** confirm every sentence above is true. Rules 12 and 13 require
> meaningful participant contribution and technical understanding, and a disclosure that
> overstates human involvement is worse than an honest one that admits heavy AI use.
> [`docs/COMPLIANCE.md`](docs/COMPLIANCE.md) tracks this and every other rule, including the
> items still open.

## Submission compliance

Rules 12 and 13 turn on the participant understanding the work, so this repo ships the
material for that rather than asserting it:

| Document | Purpose |
| --- | --- |
| [`docs/COMPLIANCE.md`](docs/COMPLIANCE.md) | Rule-by-rule audit with evidence and the open items |
| [`docs/EXPLAINER.md`](docs/EXPLAINER.md) | How to defend the model, every query, and the trade-offs |
| [`docs/GRAPH_MODEL.md`](docs/GRAPH_MODEL.md) | The schema and the queries the product depends on |

## Data

The Mars mission scenario is **synthetic**, written for this demo. No private, personal, or
paywalled data is used. The only external factual claim is the composition of the Martian
atmosphere (~95% CO₂), which is public NASA science.

## Team

- cptlight

## License

MIT
