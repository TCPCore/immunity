# Submission Pack

Copy-paste material for the Graph Hacks submission form, plus the checklist to
run through before you hit submit.

> **Confirm registration first.** Rule 1 and the published schedule make registration the
> one checkpoint you must act on yourself. Nothing in this repository substitutes for it.

---

## Tracks entered (rule 5, name *every* track)

- **Track 01, Agents That Act on Connected Data**
- **Track 02, Agent Memory and Coordination**
- **Track 03, Company Brain**

Each is a real tab in the deployed app, not a claim in a paragraph:

| Track | The tab | What it demonstrates |
| --- | --- | --- |
| 01 | **Check any claim** | A visitor's false claim propagates through real agents, severity climbs from Q4, and the cascade is contained |
| 02 | **Kernel & durability** | Kernel of truth, provenance, write-ahead log, rebuild proven identical |
| 03 | **Company brain** | Four synthetic documents become the kernel; a wrong price spreads between departments and is caught; a policy change supersedes the old fact and the previously-correct answer is flagged |

---

## One-line pitch

ImmuneGraph is a FalkorDB-backed immune system for AI agent swarms: it detects hallucination
cascades as graph traversals, dispatches an antibody that injects a sourced correction, and
remembers the outbreak so the swarm cannot be fooled twice.

## Short description (~50 words)

Multi-agent systems can be perfectly internally consistent and still be wrong: one agent
invents a claim, others derive plans from it, and the swarm acts on a lie. ImmuneGraph models
belief as a graph so unsupported claims, self-justifying cycles and cascades become Cypher
traversals, and the response is recorded as immunity.

## Long description (~200 words)

Agent memory layers store what was said. None of them notice that a swarm is getting
epistemically sick.

ImmuneGraph treats every claim, source, agent and derivation as a node in FalkorDB. Four
Cypher queries then detect the pathologies of collective belief: cycles in the support
structure (a self-justifying belief), active claims with no trusted source and no parent (an
unsupported root), contradiction clusters between agents, and cascade reach along derivation
edges.

The graph is load-bearing rather than decorative. **Severity is defined as graph reach**, Q4
walks `DERIVED_FROM` backwards from a bad root to count the claims and agents it contaminated,
so removing FalkorDB removes the concept of "how bad is this outbreak". **Trust is derived,
never mutated**, Q5 recomputes every agent's standing from exposure to unresolved infections,
so it cannot drift out of sync with the belief graph.

On detection, an antibody agent injects a correction that must cite a source, quarantines the
claim, and records the episode. When a similar claim reappears, even as a paraphrase, an
indexed fingerprint match or a vector-similarity lookup recalls the prior infection instantly,
with no cascade re-detection. Q8 measures whether antibodies actually worked, turning "the
swarm develops immunity" into a number.

## What is new here

Detection is the published part. The new piece is that the system **audits its own detector**.

Every tool in this space reports recall, how much it catches, and recall is gameable: flag
everything and it reads 100%. The audit generates adversarial probes from the swarm's own kernel
and scores both sides, precision against hand-written true restatements, autoimmunity against the
host, and attribution (did it fire at the *right* fact). Gaps it finds are written back as
`RefutedStatement` nodes through the same append-only log, then re-measured to prove the holes
closed without poisoning the kernel.

It earned its place on this codebase: with the whole suite green it measured 20% autoimmunity, the
fix took it to zero, and the obvious repair for the next gap drove it to 82% while recall still
read 100%. One number would have shipped that as a success.

## Measured results

Copy these into any form field asking what has been verified.

```
Cypher queries executed against a live instance     18 / 18
Kernel fast path (local)                            1.31 ms median, 2.25 ms p95
Detection sweep, 12-node graph (local)              20.3 ms
Rebuild from the write-ahead log                    73 writes, 0 failed, identical
Concurrent sequence allocation, 8 writers           200 / 200 unique
Conflict detection precision / recall / F1          1.00 / 1.00 / 1.00  (including after a policy revision)
Autoimmunity rate (true statements wrongly flagged) 0%
Regression suite                                    162 tests
Track qualification                                 21 / 21 checks across 3 tracks
```

Two of those deserve their condition attached, because a number without one is marketing:

**Precision and recall** are measured on a generated labelled set: falsehoods produced by mutating
the kernel's own facts, and negatives that include six hand-written true restatements phrased the
way a person would say them. It is not human-labelled production traffic.

**Sweep scaling** was measured at 1.1 s for 100 claims and 14.9 s for 1,000 in the worst case,
where every claim is an unsupported root and the sweep writes an infection for each. The fast path
stayed flat at 1.3 ms throughout. That asymmetry is why the check that blocks answers is the fast
path and the sweep is asynchronous.

Both are reproducible: `python scripts/verify_tracks.py`, `python scripts/bench_scale.py`,
`python -m immunegraph.verify_queries`, `python -m pytest`.

## Links

| Item | Value |
| --- | --- |
| Repository | <https://github.com/TCPCore/immunity>, public, default branch `main`, actively maintained |
| Demo video | **Recorded and verified**, 4:17, 1680×1200, silent with burned-in captions, a visible cursor and click pulses on each action, and an address bar. Upload it (YouTube/Loom, public or unlisted) and paste the URL here. Reproducible with `python scripts/record_demo.py`; `docs/DEMO_SCRIPT.md` is the shot list. |
| Live deployment | <https://immunity-jcssfw2knj85bkdtut3rie.streamlit.app> (see note), or local Docker / FalkorDB Cloud; see below |

> **Deployment note.** The Streamlit app is deployed from this repository. As of the last check from
> an unauthenticated client it returned `303` to `share.streamlit.io/-/auth/app`, so it may require
> a signed-in viewer depending on the app's Sharing setting. Verify in an incognito window before
> submitting. If it is not publicly reachable, rule 10's "complete local setup instructions"
> alternative is satisfied by the Docker path below.

## Setup (rule 10, a live deployment, or complete local setup instructions)

```bash
git clone https://github.com/TCPCore/immunity.git
cd immunity
docker compose up -d                                   # FalkorDB on localhost:6379
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"   # installs the package itself
cp .env.example .env                                   # optional: add OPENAI_API_KEY
.venv/bin/python -m immunegraph.verify_queries          # executes all 18 queries
.venv/bin/python -m pytest                              # 162 tests
.venv/bin/streamlit run app.py
```

`-e ".[dev]"` installs the package plus the dev tools, which is what the commands above need.
The project uses a `src/` layout, so `python -m immunegraph...` only resolves once the package
itself is installed.

`requirements.txt` installs it too, because its first line is `-e .`. That line is load-bearing
for hosted deploys: Streamlit Community Cloud runs `pip install -r requirements.txt` and then
`streamlit run app.py` with no separate install step. Without it the `src/` layout means
`import immunegraph` fails and the deployed app dies at startup.

No API key is required: with `EXTRACTOR=rule` the extraction is deterministic and every graph
query still runs for real. `EXTRACTOR=auto` uses an LLM when a key is present and falls back
to rules otherwise.

FalkorDB Cloud works too, point `FALKORDB_HOST` / `FALKORDB_PORT` / `FALKORDB_USER` /
`FALKORDB_PASSWORD` at a free instance. No code changes.

## Data statement (rule 6)

The Mars mission scenario is **synthetic**, written for this demo. No private, personal,
login-protected or paywalled data is used. The single real-world factual claim is the
composition of the Martian atmosphere, which is public NASA science and is cited in the graph
as a `Source` node pointing at <https://science.nasa.gov/mars/facts/>.

## AI assistant disclosure (rule 11)

See the full section in the README. Summary: AI coding assistants were used extensively for
implementation and documentation. The project concept, the immune-system framing, the choice
of Tracks 01, 02 and 03, the Mars scenario, the memory-taxonomy mapping and the Docker decision
were human decisions. Every Cypher query was executed against a live FalkorDB instance before
being committed, and running them, rather than assuming them, caught six defects. The team
can explain the graph model, each detection query and the trade-offs; `docs/EXPLAINER.md` is
the written form of that explanation.

> Confirm every sentence is true before submitting.

---

## Pre-submission checklist

**Required by rule 10**

- [x] Public source-code repository, verified public via the GitHub API
- [x] README with setup instructions
- [x] README with the graph data model
- [x] The Cypher queries the product depends on, `docs/GRAPH_MODEL.md` §5, `src/immunegraph/queries.py`, and the four headline queries inlined in the README
- [ ] **Demo video** recorded (4:17, captioned, cursor and address bar), **still needs uploading and a link here**
- [x] Complete local setup instructions (Docker) and a Cloud path
- [x] Tracks named, 01, 02 and 03

**Other rules**

- [x] FalkorDB is the only datastore and the graph does real work (rule 2/3)
- [x] Synthetic / public data only (rule 6)
- [x] All work began after the hackathon opened (rule 8), first commit 12:36 IST vs 00:01 IST open
- [x] AI assistant use disclosed (rule 11)
- [ ] **Rule 12/13 review complete**, see `COMPLIANCE.md` for the five concrete steps
- [x] MIT licence present
- [x] `python -m immunegraph.verify_queries` passes
- [x] `python -m pytest` passes (162 tests)

**Final five minutes**

- [x] `git push`, `origin/main` in sync with local `HEAD` (same SHA), verified with `git fetch`
- [x] README renders from the remote, fetched raw and confirmed, screenshot returns 200
- [ ] Confirm the demo video is public or unlisted-but-linkable, not private
- [ ] Re-read the AI disclosure with a critical eye
- [ ] Submit with both track names spelled exactly as published
