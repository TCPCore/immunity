# Submission Pack

Copy-paste material for the Graph Hacks submission form, plus the checklist to
run through before you hit submit.

> **Confirm registration first.** Rule 1 and the published schedule make registration the
> one checkpoint you must act on yourself. Nothing in this repository substitutes for it.

---

## Tracks entered (rule 5 — name *every* track)

- **Track 01 — Agents That Act on Connected Data**
- **Track 02 — Agent Memory and Coordination**

---

## One-line pitch

ImmuneGraph is a FalkorDB-backed immune system for AI agent swarms: it detects hallucination
cascades as graph traversals, dispatches an antibody that injects a sourced correction, and
remembers the outbreak so the swarm cannot be fooled twice.

## Short description (~50 words)

Multi-agent systems can be perfectly internally consistent and still be wrong: one agent
invents a claim, others derive plans from it, and the swarm acts on a lie. ImmuneGraph models
belief as a graph so unsupported claims, self-justifying cycles and cascades become Cypher
traversals — and the response is recorded as immunity.

## Long description (~200 words)

Agent memory layers store what was said. None of them notice that a swarm is getting
epistemically sick.

ImmuneGraph treats every claim, source, agent and derivation as a node in FalkorDB. Four
Cypher queries then detect the pathologies of collective belief: cycles in the support
structure (a self-justifying belief), active claims with no trusted source and no parent (an
unsupported root), contradiction clusters between agents, and cascade reach along derivation
edges.

The graph is load-bearing rather than decorative. **Severity is defined as graph reach** — Q4
walks `DERIVED_FROM` backwards from a bad root to count the claims and agents it contaminated,
so removing FalkorDB removes the concept of "how bad is this outbreak". **Trust is derived,
never mutated** — Q5 recomputes every agent's standing from exposure to unresolved infections,
so it cannot drift out of sync with the belief graph.

On detection, an antibody agent injects a correction that must cite a source, quarantines the
claim, and records the episode. When a similar claim reappears — even as a paraphrase — an
indexed fingerprint match or a vector-similarity lookup recalls the prior infection instantly,
with no cascade re-detection. Q8 measures whether antibodies actually worked, turning "the
swarm develops immunity" into a number.

## Links

| Item | Value |
| --- | --- |
| Repository | <https://github.com/TCPCore/immunity> — public, 30 commits on `main` |
| Demo video | **Recorded and verified** — 90.5s, 1680×1200, silent with burned-in captions and per-beat panel highlighting. Upload it (YouTube/Loom, public or unlisted) and paste the URL here. Reproducible with `python scripts/record_demo.py`; `docs/DEMO_SCRIPT.md` is the shot list. |
| Live deployment | Local Docker / FalkorDB Cloud; see below |

## Setup (rule 10 — a live deployment, or complete local setup instructions)

```bash
git clone https://github.com/TCPCore/immunity.git
cd immunity
docker compose up -d                                   # FalkorDB on localhost:6379
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"   # installs the package itself
cp .env.example .env                                   # optional: add OPENAI_API_KEY
.venv/bin/python -m immunegraph.verify_queries          # executes all 13 queries
.venv/bin/python -m pytest                              # 85 tests
.venv/bin/streamlit run app.py
```

`-e ".[dev]"` is required rather than `requirements.txt`: the project uses a `src/` layout, so
`python -m immunegraph...` only resolves once the package is installed. Installing only the
requirements leaves the package missing and every command fails with `ModuleNotFoundError`.

No API key is required: with `EXTRACTOR=rule` the extraction is deterministic and every graph
query still runs for real. `EXTRACTOR=auto` uses an LLM when a key is present and falls back
to rules otherwise.

FalkorDB Cloud works too — point `FALKORDB_HOST` / `FALKORDB_PORT` / `FALKORDB_USER` /
`FALKORDB_PASSWORD` at a free instance. No code changes.

## Data statement (rule 6)

The Mars mission scenario is **synthetic**, written for this demo. No private, personal,
login-protected or paywalled data is used. The single real-world factual claim is the
composition of the Martian atmosphere, which is public NASA science and is cited in the graph
as a `Source` node pointing at <https://science.nasa.gov/mars/facts/>.

## AI assistant disclosure (rule 11)

See the full section in the README. Summary: AI coding assistants were used extensively for
implementation and documentation. The project concept, the immune-system framing, the choice
of Tracks 01 and 02, the Mars scenario, the memory-taxonomy mapping and the Docker decision
were human decisions. Every Cypher query was executed against a live FalkorDB instance before
being committed, and running them — rather than assuming them — caught five defects. The team
can explain the graph model, each detection query and the trade-offs; `docs/EXPLAINER.md` is
the written form of that explanation.

> Confirm every sentence is true before submitting.

---

## Pre-submission checklist

**Required by rule 10**

- [x] Public source-code repository — verified public via the GitHub API
- [x] README with setup instructions
- [x] README with the graph data model
- [x] The Cypher queries the product depends on — `docs/GRAPH_MODEL.md` §5, `src/immunegraph/queries.py`, and the four headline queries inlined in the README
- [x] **Demo video** recorded (90.5s, captioned, verified beat by beat) — **still needs uploading and a link here**
- [x] Complete local setup instructions (Docker) and a Cloud path
- [x] Tracks named — 01 and 02

**Other rules**

- [x] FalkorDB is the only datastore and the graph does real work (rule 2/3)
- [x] Synthetic / public data only (rule 6)
- [x] All work began after the hackathon opened (rule 8) — first commit 12:36 IST vs 00:01 IST open
- [x] AI assistant use disclosed (rule 11)
- [ ] **Rule 12/13 review complete** — see `COMPLIANCE.md` for the five concrete steps
- [x] MIT licence present
- [x] `python -m immunegraph.verify_queries` passes
- [x] `python -m pytest` passes (85 tests)

**Final five minutes**

- [x] `git push` — 25 commits verified on `origin/main`, same SHA as local `HEAD`
- [x] README renders from the remote — fetched raw and confirmed, screenshot returns 200
- [ ] Confirm the demo video is public or unlisted-but-linkable, not private
- [ ] Re-read the AI disclosure with a critical eye
- [ ] Submit with both track names spelled exactly as published
