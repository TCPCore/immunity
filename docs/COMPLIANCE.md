# Hackathon Compliance Audit

Rule-by-rule audit against the **Graph Hacks: Context for AI Agents** rules
(WeMakeDevs × FalkorDB), as published at
<https://www.wemakedevs.org/hackathons/falkordb/rules>.

Last audited: **2026-10-15 12:45 IST**. Re-check before submitting, the rules page
states the rules, schedule and prize breakdown may be updated before the event.

Legend: ✅ satisfied · ⚠️ action required · ⛔ not yet done

---

## Critical path items still open

✅ **The push is done.** All 25 commits are on `main`, verified three ways: `origin/main`
and `HEAD` resolve to the same SHA, `git rev-list --count origin/main` returns 25, and every
key file returns HTTP 200 from the GitHub contents API. The README and its screenshot both
render from the remote.

| # | Item | Owner | Why it blocks |
| --- | --- | --- | --- |
| 1 | **Confirm registration** | human | Rule 1. The schedule says registration is the one checkpoint you must act on. An unregistered team cannot submit. |
| 2 | **Host the demo video** and link it | human | Rule 10 requires "a demo video showing the working project". It is recorded and verified (90.5s, captioned, reproducible via `scripts/record_demo.py`); it now needs uploading to YouTube/Loom and linking from `SUBMISSION.md`. Consider re-recording with narration, a silent captioned cut satisfies the rule but a narrated one argues rules 12/13 better. |
| 3 | **Complete the rule 12/13 review** | human | Rules 12 and 13. You must be able to explain the model and every query, and demonstrate meaningful contribution. See `EXPLAINER.md`. |
| 4 | **Finalise the AI disclosure** | human | Rule 11. The wording in the README must be true when you submit it. |
| 5 | **Name both tracks at submission** | human | Rule 5: enter 01 and 02, and name *every* track you are entering. |

## What is already done and verified

| Claim | Evidence |
| --- | --- |
| FalkorDB is the only datastore and does the detection | 13 Cypher queries, all executed against a live instance |
| Every query actually runs | `python -m immunegraph.verify_queries` → "All non-optional queries executed and returned expected shapes" |
| The product works end to end | `python -m immunegraph.demo` exits 0; 160 tests pass, twice consecutively |
| The documented quickstart works for a judge | verified by exporting a fresh clone with `git archive` and following the README verbatim against a recreated database |
| Opening the app before Docker is running | shows "Cannot reach FalkorDB … start it with docker compose up -d", not a traceback |
| High-concurrency reads and writes (Track 02) | 8 concurrent writers, 200 writes: 200/200 unique sequence numbers vs 37/200 for the naive read-then-write. See `tests/test_concurrency.py` |
| The UI works | 5 headless `AppTest` tests execute `app.py`, click through the mission and assert recall fires |
| The repo is public and fully pushed | verified via the GitHub API; `origin/main` and `HEAD` are the same commit |
| No credentials in history | `git log --diff-filter=A` shows no `.env` ever committed |

---

## Rule-by-rule

### Rule 1, Solo or team of up to 4, one team per person
✅ Solo entry as `cptlight`.
⚠️ **Confirm you are actually registered** on the WeMakeDevs site. Nothing in this
repository substitutes for registration.

### Rule 2, FalkorDB must be the primary graph database and support a central part
✅ FalkorDB is the *only* datastore. There is no secondary database, cache, or
SQL store. Every detection, severity, trust and recall computation is Cypher
(`src/immunegraph/queries.py`).

### Rule 3, The graph has to do real work; not decoration
✅ This is the rule the architecture was designed around, and it is worth being able
to defend.

The product's four core capabilities are graph traversals, not lookups:

| Capability | Cypher | If FalkorDB were removed |
| --- | --- | --- |
| Cycle detection (self-justifying belief) | Q1 `[:SUPPORTED_BY\|DERIVED_FROM*2..5]` | no traversal engine ⇒ gone |
| Cascade reach → **infection severity** | Q4 `[:DERIVED_FROM*1..5]` | severity is *defined* as graph reach ⇒ gone |
| Trust decay by exposure | Q5 (two-pattern exposure) | trust is *derived* from association ⇒ gone |
| Immunity recall | Q6 indexed fingerprint / Q7 HNSW vector | recall *is* a graph lookup ⇒ gone |

Deliberate choices that keep FalkorDB load-bearing:

- **Severity is computed from graph reachability (Q4), not from an LLM.** Remove the
  graph and the notion of "how bad is this outbreak" ceases to exist.
- **Trust is derived in Cypher (Q5), never mutated in Python.** It cannot drift out of
  sync with the belief graph because it is not stored independently.
- **No Python fallback computes cycles or reach.** The only Python-side scoring is
  `models.similarity()` in `Q7B_RECALL_CANDIDATES`, and even there the *candidate set*
  is produced by the graph; Python only ranks what FalkorDB returned. This is
  documented in `GRAPH_MODEL.md` §5 Q7.

### Rule 4, Run FalkorDB however suits you (Docker / self-hosted / Cloud)
✅ Local Docker via `docker-compose.yml` (`falkordb/falkordb:latest`).
✅ FalkorDB Cloud is also a supported path with no code change, the connection is
environment-driven (`src/immunegraph/config.py`).

### Rule 5, Open-ended within three tracks; name every track at submission
✅ Entering **all three**, each kept unbroken in the README so an automated checker can match it:
- **Track 01, Agents That Act on Connected Data**
- **Track 02, Agent Memory and Coordination**
- **Track 03, Company Brain**

⚠️ All three must be named in the submission form. The README states all three.

Evidence that each is genuinely covered, not merely named. Run `python scripts/verify_tracks.py`
for the measured version, which checks the graph rather than the prose:

| Track | Where it is demonstrated | What it proves |
| --- | --- | --- |
| 01 | **Check any claim** tab, and the antibody path in the Mars demo | Agents *act* on the graph: an infection is opened, an antibody injects a sourced correction, the claim is quarantined, and `contain_cascade` invalidates every derivation. The visitor's claim spreads through real agents before it is arrested. |
| 02 | **Kernel & durability** tab, `tests/test_concurrency.py`, `tests/test_durability.py` | Three memory kinds (episodic / semantic / procedural), a kernel of truth beside the infection record, trust derived rather than stored, 200/200 concurrent sequence allocation, and a rebuild from the write-ahead log proven identical including node ids. |
| 03 | **Company brain** tab, `src/immunegraph/company.py` | Four synthetic documents become the kernel; a wrong price spreads between departments and is quarantined against the pricing document; a policy change supersedes the old fact and the previously correct answer is re-judged. |

Track 02's memory-taxonomy mapping is spelled out in `../AGENT_BRIEF.md` §2
(episodic/semantic/procedural memory, persistent history, shared state and handoffs,
concurrency, per-tenant memory).

### Rule 6, Only public, synthetic, or your own data
✅ The Mars mission scenario is **synthetic**, written for this demo.
✅ The single real-world factual claim, the Martian atmosphere is ~95% CO₂, is public
NASA science and is cited as a `Source` node pointing at
<https://science.nasa.gov/mars/facts/>.
✅ No private, login-protected, paywalled, personal or otherwise restricted data is
loaded. The only external network calls are to the LLM API for extraction, and no
third-party or personal data is ingested.

### Rule 7, Hackathon window
✅ Window is Oct 15 12:01 AM IST → Oct 18 11:59 PM IST 2026.

### Rule 8, Main coding and design work must begin after the start
✅ **Satisfied, and provable from git history.**

| Event | Timestamp |
| --- | --- |
| Hackathon opened | 2026-10-15 **00:01 IST** |
| Workspace first inspected | 2026-10-15 12:26 IST, empty, no prior code |
| First commit | 2026-10-15 **12:36 IST** |

All implementation began ~12.5 hours *after* the event opened. Rule 8 permits
preparation of notes and graph-model sketches beforehand; in any case no
pre-hackathon code exists, as the workspace was empty at session start.

### Rule 9, Frameworks, libraries, public APIs and datasets allowed
✅ FalkorDB, the official `falkordb` Python client, Streamlit, `streamlit-agraph`,
`openai`, `numpy`, `pytest`. All open-source or public APIs.

### Rule 10, Required submission contents
| Required | Status | Where |
| --- | --- | --- |
| Public source-code repository | ✅ | [github.com/TCPCore/immunity](https://github.com/TCPCore/immunity), verified public via the GitHub API, 25 commits on `main` |
| README with setup instructions | ✅ | `../README.md` |
| README with graph data model | ✅ | `../README.md` summary + `GRAPH_MODEL.md` |
| The Cypher queries the product depends on | ✅ | `GRAPH_MODEL.md` §5 and `src/immunegraph/queries.py` |
| Demo video | ✅ recorded, ⚠️ needs hosting | **90.5s at 1680×1200.** Silent, with burned-in captions and the relevant panel outlined on each beat, so it explains itself without narration. Produced from the live UI by the committed `scripts/record_demo.py`. Deliberately not tracked in git (`*.mp4` is ignored), host it and link it from `SUBMISSION.md`. |
| Live deployment, or complete local setup | ✅ | `../README.md` Option A (Docker) and Option B (Cloud) |
| The track being entered | ✅ | `../README.md`, tracks 01, 02 and 03 |

### Rule 11, Disclose AI coding assistant use
⚠️ Section exists in `../README.md` and **must be reviewed and made true by the human
before submission.** See the note below.

### Rule 12, Participants must understand the code and be able to explain it
⚠️ **Action required by the human.** For every query you must be able to answer: what
does it find, why is it written this way, what breaks without it. `EXPLAINER.md` is
written for exactly this, but reading it is not the same as understanding it. Run
`python -m immunegraph.verify_queries`, change a query, break it, watch it fail.

### Rule 13, No fully-AI-generated project without meaningful contribution
⚠️ **Highest-risk rule for this project.** See "Honest assessment" below.

### Rule 14, Prize pool and awards
✅ Noted. $20,000 total, **one prize per track**, and every member of a winning
team receives it. Track 01: iPhone 18 Pro. Track 02: PS5 with GTA VI.
Track 03: M6 Mac Mini.

The published rules list **no side prize for blogging**. The blog post is written
because it is the clearest explanation of the work and judges read it, not
because there is a prize attached. (An earlier draft of these notes claimed two
side prizes; that is not in the current rules and has been removed.)

### Rule 15, IP belongs to the participant
✅ `LICENSE` (MIT) is present.

### Rules 16-17, Conduct, no plagiarism or judging manipulation
✅ Original work. No third-party code copied. Synthetic data. No contact with judges
beyond the public Discord.

### Rule 18, Failure to comply may result in disqualification
Understood.

---

## Honest assessment of rules 12 and 13

These two rules are the real risk, and no amount of documentation removes it. An AI
coding assistant wrote the majority of the implementation in this repository, at the
human's direction and against design decisions the human set (the immune-system
concept, the two tracks, the Mars scenario, the local-Docker decision). Rule 13
targets projects where there is **no meaningful participant contribution, verification,
or technical understanding**, so the defence is not a better-written disclosure, it is
actual verification.

Concrete, non-negotiable steps before submitting:

1. Read `EXPLAINER.md` end to end and be able to reproduce its arguments unaided.
2. Run `python -m immunegraph.verify_queries` yourself and read the output for every
   query, not just the pass/fail summary.
3. Deliberately break a query (flip `ORDER BY score ASC` to `DESC` in `Q7`) and explain
   why the results get worse. The verifier will not catch that, you have to.
4. Change something real of your own: a query, a threshold, a node property. Commit it.
   The contribution should be visible in the history, not just asserted in a document.
5. On camera, explain the graph model and one query without notes.

The disclosure in the README is drafted but deliberately left marked as needing your
confirmation, because only you can make its claims true.
