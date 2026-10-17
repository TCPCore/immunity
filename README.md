# ImmuneGraph — a self-healing memory for AI agent swarms

**A FalkorDB-backed immune system that catches hallucination cascades in multi-agent
systems, corrects them, and remembers them so the swarm develops immunity.**

Built for **Graph Hacks: Context for AI Agents** (WeMakeDevs × FalkorDB, Oct 15–18 2026).

> **Tracks entered:**
>
> - **Track 01 — Agents That Act on Connected Data**
> - **Track 02 — Agent Memory and Coordination**
>
> Each track name is kept unbroken on its own line so an automated submission
> checker can match it exactly.

---

## The 30-second version

Agent memory layers store **what was said**. ImmuneGraph stores **what supports what** — so
the pathologies of collective belief become graph traversals a Cypher query can detect.

Three agents plan a Mars mission. One asserts something with no source. The other two reason
*correctly* from it. Nothing was stored incorrectly, and the swarm is now acting on a lie.

ImmuneGraph catches it, corrects it, and then catches it again instantly when it comes back —
as a *paraphrase*. In one verified run from a clean graph:

| Beat | Result | Produced by |
| --- | --- | --- |
| Cascade | 3 claims, 2 derivation hops | `Message` → `Claim` extraction |
| Detection | 1 infection, **severity 6.0** = 1 + 2 claims + 3 agents | **Q4**, graph reach |
| Response | antibody cites NASA, root quarantined | `Antibody` + `INJECTS` |
| Trust | all three agents → **0.85** | **Q5**, derived not stored |
| Recall | paraphrase matched at **0.75 similarity** | **Q6/Q7**, indexed |
| Outcome | both outbreak types contained, **0 recurred** | **Q8** |

**Remove FalkorDB and the product does not work.** Severity *is* graph reachability (Q4);
trust *is* derived from association (Q5); the immune memory *is* the
`Infection`→`TARGETS`→`Claim` subgraph. Nothing here is a visualisation drawn over ordinary
storage.

![ImmuneGraph — conversation, live belief graph and immune dashboard](docs/images/immunegraph-ui.png)

*After the Mars run and the immunity-recall test. The original claim and its later paraphrase
are both quarantined (dark red). The Analyst's and Planner's claims are amber — **support
invalidated** — because their only justification was the claim just refuted; they are not
proven false, but they are no longer trustworthy. Peak severity is 6.0, both outbreaks are
contained, and trust has decayed to **0.70** for the Researcher (who asserted the falsehood
*and* its repeat) and **0.85** for the Analyst and Planner, who each only inherited it.*

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

## The kernel of truth — memory of what is *right*

Everything above records what was **wrong**: an `Infection`, a quarantined `Claim`, a trust
score that dropped. Useful, but incomplete. A swarm that only remembers its mistakes learns
nothing it can reuse — every outbreak leaves a scar and the next one is caught the same
expensive way.

So when an antibody produces a correction that **cites a source**, the canonical form of that
correction becomes a `KernelFact`: linked to the source that backs it, and to the antibody
that established it. From then on every new claim is judged against the kernel.

| | Detection (Q1–Q8) | Sanitisation (K1–K5) |
| --- | --- | --- |
| Finds | lies the swarm has **never seen** | lies it **already knows** |
| Method | traversal over the support structure | lexical match against facts |
| Cost | extraction + full scan — **3.1s** for the cascade | **~5ms**, no model call |

That contrast is the whole point, and it is measured rather than asserted: every verdict
carries its own elapsed time, shown in the UI and printed by the demo.

**Only sourced corrections become facts.** If the antibody cannot cite anything, it says so
and establishes nothing — "no source supports this" is a meta-statement, not a truth worth
remembering, and inventing certainty would just be a new infection.

Provenance is why this lives in the graph rather than a side table. *Which antibody* taught
the swarm a fact, *citing what*, and *which claims conflict with it* are all edges, and
answering "why is this claim quarantined?" is a traversal:

```cypher
MATCH (k:KernelFact {id: $fact_id})
OPTIONAL MATCH (ab:Antibody)-[:ESTABLISHES]->(k)
OPTIONAL MATCH (k)-[:DERIVED_FROM]->(s:Source)
RETURN k.text, ab.id AS established_by, s.name AS source, s.url AS source_url
```

> **A bug worth mentioning.** The first version of the kernel-health query counted with
> `count(*)`, which under `OPTIONAL MATCH` counts *rows* rather than *edges* — so an empty
> kernel reported one conflict that did not exist, in exactly the direction that flattered
> the project. Named-relationship counts ignore nulls; `count(*)` does not. See
> [`docs/GRAPH_MODEL.md`](docs/GRAPH_MODEL.md) §5b.

---

## Durability — the graph is a projection

**FalkorDB Cloud's free tier has no persistence and no backups.** Instances are stopped after
a day idle and deleted after seven. A kernel of truth that evaporates is not much of a truth,
and a judge clicking a dead link is worse than no link.

So every write is appended to a **write-ahead log** *before* it is applied, and the graph can
be rebuilt from that log at any time. FalkorDB is the live queryable view; the log is what
survives.

Press **♻️ Rebuild from log** and it wipes the graph and reconstructs it, then reports whether
the result is *identical* rather than merely plausible:

```
Rebuilt 73 writes from the log — the graph is identical to the one before
the wipe (20 nodes).
```

**Why the log stores statements rather than semantic events.** Events such as
`ClaimAsserted {text: ...}` read better, but replay then has to reconstruct *which* write that
implied, and any divergence between the event vocabulary and the writer's behaviour shows up
as a graph that quietly differs from the original. Statements cannot diverge — replaying them
*is* the original write.

That matters most for ids. Infection and antibody ids are `uuid4`, so a replay that regenerated
them would produce a different graph with every reference broken. Because the log stores the
fully-substituted statement, the generated id is captured with it.

Replay is *proven*, not assumed: `graph_fingerprint()` compares node ids, relationship counts
and claim statuses before and after, and [`tests/test_durability.py`](tests/test_durability.py)
asserts they match — over the whole Mars arc, twice in a row, and from an exported seed.

> **The fingerprint earned its place immediately.** The first rebuild looked fine by every
> superficial measure, but two claims silently reverted from `suspect` to `active`. The cause:
> the cascade-containment update returns ids, so it *read* like a query and had been written
> with `rows()` — which is **not logged**. It worked, and a rebuild lost it. There is now a
> structural test that fails if any write hides inside a read helper.

### Cold starts

[`data/seed_writes.jsonl`](data/seed_writes.jsonl) is a committed 73-write log of the Mars run.
Point `SEED_LOG` at it and a freshly deployed app rebuilds the whole story on first load — no
clicking, nothing to go stale:

```bash
SEED_LOG=data/seed_writes.jsonl
```

For genuine cross-restart durability the log needs durable storage — a mounted volume, object
storage, or a paid FalkorDB tier. On Streamlit Community Cloud the filesystem is ephemeral, so
the seed is what makes a cold start deterministic.

---

## Graph model

Nodes: `Tenant` · `Session` · `Message` · `Agent` · `Claim` · `Source` · `Infection` · `Antibody` · `KernelFact`

| Memory type | Nodes | Role |
| --- | --- | --- |
| Episodic | `Message`, `Session` | append-only conversation history |
| Semantic | `Claim`, `Source`, `Agent`, `KernelFact` | durable beliefs, who holds them, and what the swarm has established as true |
| Procedural | `Antibody`, `Infection` | how the swarm responds to a class of failure |

Relationships: `SPOKE` · `MENTIONS` · `ASSERTS` · `SUPPORTED_BY` · `DERIVED_FROM` ·
`CONTRADICTS` · `REPEATS` · `TRUSTS` · `HANDOFF_TO` · `TARGETS` · `SPREAD_TO` ·
`NEUTRALIZES` · `INJECTS` · `ESTABLISHES` · `CONFLICTS_WITH` · `ALIGNED_WITH` · `SUPERSEDES`

The full schema, indexes, and all eighteen load-bearing Cypher queries are in
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
git clone https://github.com/TCPCore/immunity.git && cd immunity
docker compose up -d                                   # FalkorDB on localhost:6379
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"   # installs the package itself
cp .env.example .env                                   # optional; only for the LLM extractor
.venv/bin/python -m immunegraph.verify_queries          # proves every Cypher query runs
.venv/bin/streamlit run app.py
```

> **Do not skip the `-e ".[dev]"` install.** This project uses a `src/` layout, so
> `python -m immunegraph...` only resolves once the package is installed. Installing
> `requirements.txt` on its own puts the *dependencies* in place but leaves the *package*
> missing, and every command below then fails with `ModuleNotFoundError: No module named
> 'immunegraph'`. This was caught by following this README verbatim in a clean checkout.

No API key is required. With `EXTRACTOR=rule` extraction is deterministic and every graph
query still runs for real; `EXTRACTOR=auto` uses an LLM when a key is present and silently
falls back to rules otherwise.

`EXTRACTOR` is validated at startup: an unrecognised value is rejected rather than quietly
falling through to `auto`, and `EXTRACTOR=llm` without a key fails immediately with an
explanation instead of raising a provider error mid-conversation.

### Option B — FalkorDB Cloud

Create a free instance at [app.falkordb.cloud](https://app.falkordb.cloud), then set
`FALKORDB_HOST`, `FALKORDB_PORT`, `FALKORDB_USER`, `FALKORDB_PASSWORD` in `.env`.
No other change is needed — the connection layer is environment-driven.

> **Platform note.** `falkordblite` (embedded) is *not* used. Its published macOS wheels are
> tagged universal2 but contain **arm64-only** `redis-server` and `falkordb.so`, so it fails
> on x86_64 Macs with `[Errno 86] Bad CPU type in executable`. This is an upstream packaging
> bug, not a missing dependency. Use the container or Cloud.

### Run the UI

```bash
.venv/bin/streamlit run app.py
```

Three panels: the conversation, the live belief graph, and the immune dashboard. The sidebar
runs the Mars mission, re-injects the false claim to demonstrate immunity recall, and resets
the graph. Node colour encodes label and node size encodes infection severity, so the blast
radius of an outbreak is visible at a glance.

### Run the demo headless

```bash
.venv/bin/python -m immunegraph.demo            # add --pause 1.5 when recording
```

`demo.py` exits non-zero if immunity recall does not fire, no infection is detected, or no
antibody responds — so it doubles as a CI check and as the script for the demo video.

### Demo video

[`scripts/record_demo.py`](scripts/record_demo.py) produces the demo video from the running
app: it drives the UI over the Chrome DevTools Protocol, burns in a caption bar, outlines the
relevant panel on each beat, and encodes H.264. The result is deliberately not committed
(`*.mp4` is gitignored) — host it and link it here. [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md)
is the shot list, and `scripts/demo_dry_run.py` asserts that every number the script quotes is
the number that actually appears at that beat.

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
10. The antibody's sourced correction becomes a **`KernelFact`** (K1). A later claim is
    judged against it (K2) and, if it conflicts, downgraded — **in ~5ms with no model call**.
    The swarm is measurably harder to fool than it was at step 1.

---

## Verification

A Cypher query that has never been executed is not a feature. Three layers of proof:

```bash
.venv/bin/python -m immunegraph.verify_queries   # every query, against a live instance
.venv/bin/python -m pytest                       # 106 tests
.venv/bin/python -m immunegraph.demo             # the full arc; exits non-zero on failure
```

- **`verify_queries.py`** seeds a fixture graph containing a cascade, a 3-hop epistemic cycle,
  a contradiction cluster, two infections, two antibodies and embeddings, then executes all
  13 queries and checks the shape of each result. The optional vector path reports `SKIP`
  rather than failing on builds without vector support.
- **Unit tests** cover fingerprinting, contraction handling, similarity, source
  canonicalisation and severity.
- **Integration tests** run the whole Mars arc against a real instance on throwaway graphs,
  asserting severity 6.0, a spread of 2 claims, a NASA-citing antibody, all three agents
  decaying to 0.85, paraphrase recall at 0.75, and both outbreak types contained. Tenant
  isolation is covered for Track 02.
- **Concurrency tests** run 8 writers in parallel against shared state. The naive
  read-then-write sequence allocation produced **37 distinct sequence numbers out of 200
  writes** (163 collisions); allocating with a write gives 200/200. See
  [`tests/test_concurrency.py`](tests/test_concurrency.py).
- **UI tests** execute `app.py` in a real Streamlit runtime via `AppTest`, click through the
  mission, re-inject twice, and assert the run replays deterministically.

Running the code rather than assuming it has caught five defects that reading the source
would not have: a silent `ORDER BY` inversion in the vector recall path (FalkorDB returns a
cosine *distance* despite the option being named `similarityFunction`), missing `vecf32()`
wrapping, a false-positive contradiction caused by a conditional being read as opposing its
own premise, a case-sensitive source pattern that dropped citations, and the concurrency
collision above.

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
| [`docs/SUBMISSION.md`](docs/SUBMISSION.md) | Copy-paste text for the submission form and the final checklist |
| [`docs/EXPLAINER.md`](docs/EXPLAINER.md) | How to defend the model, every query, and the trade-offs |
| [`docs/GRAPH_MODEL.md`](docs/GRAPH_MODEL.md) | The schema and the queries the product depends on |
| [`docs/DEPLOY.md`](docs/DEPLOY.md) | Getting a clickable live link: FalkorDB Cloud + Streamlit |
| [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md) | Shot-by-shot script for the demo video |
| [`docs/BLOG_POST.md`](docs/BLOG_POST.md) | Draft write-up for the best-blog-post side prize |

## Data

The Mars mission scenario is **synthetic**, written for this demo. No private, personal, or
paywalled data is used. The only external factual claim is the composition of the Martian
atmosphere (~95% CO₂), which is public NASA science.

## Team

- cptlight

## License

MIT
