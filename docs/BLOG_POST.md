# I built an immune system for my agent swarm, then audited it and found it was wrong 20% of the time

If you run multi-agent systems, you have probably hit this and blamed something else.

Agent A says "Mars has breathable oxygen at night." Agent B, whose job is to reason, says "then we don't need helmets." Agent C, whose job is to plan, says "then we can skip life support."

Three agents. No exceptions. No errors. Every inference valid. A plan that kills the crew.

The failure is not that an LLM hallucinated. That happens, and we have tools for it. The failure is that **the swarm manufactured consensus**. One agent invented something, two others derived from it correctly, and now three agents agree. Agreement is exactly what we use as a signal of truth.

The standard fix, add a memory layer, does not touch this. Memory layers are very good at answering "what was said". They persist it, embed it, retrieve it. None of them answer "is this swarm getting epistemically sick".

I built ImmuneGraph to answer that. Then I built a way to audit it, and the audit found a real defect I would have shipped.

## What the solution actually is

Stop storing claims as records. Store them as a graph where the edges are the justification.

A claim is supported by a source. A claim is derived from another claim. A claim contradicts another claim. An agent asserts a claim.

Once belief has that shape, the pathologies stop being vague and become graph traversals:

```
PATHOLOGY              GRAPH SHAPE
circular support       a cycle in SUPPORTED_BY / DERIVED_FROM
unsupported claim      a claim with no Source and no parent
contradiction          a CONTRADICTS edge between two asserted claims
cascade                reachability along DERIVED_FROM back to a bad root
```

That is the whole idea. Everything else in this post is consequence.

The implementation is FalkorDB, a graph database that speaks Redis and a subset of openCypher. Seven node types, eleven relationships. The important edge points backwards:

```
(Claim)-[:DERIVED_FROM]->(Claim)     child -> parent
```

That direction means every cascade query is one backward traversal:

```cypher
MATCH (root:Claim {id: $root_claim_id})
OPTIONAL MATCH (down:Claim)-[:DERIVED_FROM*1..5]->(root)
WITH root, collect(DISTINCT down) AS downs
WITH root, downs, downs + [root] AS members
UNWIND members AS member
OPTIONAL MATCH (a:Agent)-[:ASSERTS]->(member)
RETURN root.id, size(downs) AS infected_claims, count(DISTINCT a) AS exposed_agents
```

That query is the product. **Severity is defined as its output.** Two contaminated claims and three exposed agents is severity 6.0. Delete the database and "severity" has no meaning, which is the test of whether a graph is doing real work or just being drawn.

Same principle for trust. A naive design decrements a counter when an agent gets blamed. Then it drifts, because you maintain the same fact in two places. Instead trust is recomputed from exposure, and the second clause below is what makes it interesting:

```cypher
MATCH (a:Agent)
OPTIONAL MATCH (a)-[:ASSERTS]->(c:Claim)<-[:TARGETS]-(i1:Infection)
  WHERE i1.status IN ['active', 'contained']
OPTIONAL MATCH (a)-[:ASSERTS]->(d:Claim)-[:DERIVED_FROM*1..5]->(:Claim)<-[:TARGETS]-(i2:Infection)
  WHERE i2.status IN ['active', 'contained']
WITH a, collect(DISTINCT i1) + collect(DISTINCT i2) AS raw
WITH a, [x IN raw WHERE x IS NOT NULL] AS infs
RETURN a.name, a.base_trust - (0.15 * size(infs)) AS effective_trust
```

You can assert an infected claim, or you can assert something derived from one. In the demo the Planner drops to 0.85 trust despite never touching the false claim, because it inherited the infection through the chain. Nobody had to write that rule down twice, because there is only one copy of the truth.

## Two memories, not one

This is the design decision I would defend hardest, and it is the part most systems skip.

The usual approach tracks **one** thing: what went wrong. A blocklist, an alert, a flagged record.

That leaves the swarm unable to answer the next question. Fine, you caught the lie. What should it believe instead?

So there are two memories with opposite polarity. `Infection` and quarantined `Claim` nodes record what was **wrong**. A `KernelFact` records what is now held **true**, written by the antibody that established it, citing the source and the claim it refutes.

The payoff is speed, and it is measurable. Detecting a cascade is a graph search that grows with the graph. Checking a new claim against the kernel is a comparison against a handful of facts. Against a local FalkorDB instance:

```
kernel fast path   1.31 ms median, 2.25 ms p95
detection sweep    20.3 ms
```

Roughly 15x, and every correction moves work from the expensive path to the cheap one. That is what immunity should do. A system that only detects pays the expensive path forever, and gets slower as it learns more.

## How this differs from the tooling you already use

Most of the ecosystem around agent memory answers one question: what was said.

```
SYSTEM                      ANSWERS                          LEAVES OPEN
vector memory               what was said                    no support structure
(Mem0, Zep, Letta)                                           contradictions coexist

GraphRAG                    what is related                  the graph is a retrieval
(Microsoft, Neo4j, FalkorDB)                                 index, not a model of belief

guardrails                   is this output acceptable        one output at a time; no
(NeMo, Guardrails AI)                                        notion of derivation

hallucination scoring        does this answer match           pointwise; cannot tell "wrong"
(Galileo, Patronus, Vectara) its context                      from "wrong and contagious"

observability                what happened                    not what was justified
(LangSmith, Langfuse)
```

Putting justification in the graph is the difference, and three things follow from it that the above cannot do:

**Severity becomes computable.** Not a model's confidence score, but reach through the derivation graph.

**Trust becomes derivable.** An agent's reliability is a function of what it asserted and what those claims descended from, computed on read.

**Why becomes a query.** "Which source justified this, which antibody established this fact, what did we believe last week" are traversals, not reconstructions from logs.

This builds on published work and I want to be straight about that: dependency-graph cascade modelling for LLM collaboration, and truth maintenance from the 1970s. Knowing the shape of the problem is not the hard part. Making it run, measuring it, and finding where it breaks is.

## Making it real: three things you can click

I did not want a diagram of a concept. Three working paths:

**1. The scripted cascade.** Three agents plan a Mars mission. The first asserts something with no source. The other two reason correctly. The swarm detects the unsupported root, opens an infection, dispatches an antibody citing NASA, quarantines the claim, and invalidates the two derivations. Severity 6.0.

**2. Anyone can submit a claim.** This matters, because a scripted demo only proves the mechanism works on inputs you chose. You type a statement, it is grounded against Wikipedia (free, no API key, public data), and then the same machinery runs. The infection genuinely spreads through the Analyst and Planner, because those agents really do derive claims from yours, with real `DERIVED_FROM` edges. Severity climbs 2.0 to 4.0 to 6.0 because it is recomputed from the graph after each write, not because a script says so.

```
Visitor asserts: "Venus has a breathable atmosphere"
Sanitised against the kernel: no stored fact matches yet
Public source consulted: Atmosphere of Venus
Infection open, severity 2.0
Analyst reasons from it, severity 4.0
Planner reasons from it, severity 6.0
Antibody dispatched, citing the public source
Quarantined, cascade contained
```

**3. A company brain.** Four synthetic documents load as sources, and the facts they establish become kernel facts linked to them. A sales agent invents a price, support repeats it with a real derivation edge, and the infection is contained by an antibody citing the pricing document. Then the price actually changes, the old fact is superseded, and the answer that was correct last week gets flagged.

One mechanism, three domains, no special cases.

## The part I think is genuinely new: measuring the other side

Every system in this space reports coverage, meaning how much it catches. That number is gameable. A detector that flags every statement scores 100% and is useless.

The other side has a name. A recent paper on agent immune systems lists the **Autoimmunity Rate**, the false-positive intervention rate, among the field's open challenges. It is not defined, computed, or used there.

So I built it, paired it with coverage, and added a third measure: did the defence fire at the right fact, or merely at one. Then I pointed all three at my own code.

```
COVERAGE       generated falsehoods the defence catches
AUTOIMMUNITY   TRUE statements it wrongly flags
ATTRIBUTION    did it fire at the RIGHT fact
PRECISION      of the flags raised, how many were false
RECALL         of the falsehoods, how many were flagged
```

The probes are generated from the system's own knowledge base: perturbed numbers, transplanted numbers, substituted entities, and truth-preserving paraphrases that must not fire. No labelled dataset required. Your knowledge base is the test set. That makes the number about your corpus rather than someone else's benchmark.

## What it found, with the whole suite green

Two things, and the second is the argument for the metric.

**First, the defence was subject-blind.** The claim "Enterprise costs 499 USD per seat per year" is true. It was judged **conflicting**, because it numerically mismatches the Team plan's fact and nothing checked which entity the claim was about.

```
AUTOIMMUNITY RATE: 2/10 = 20% of true statements wrongly flagged
```

Binding a conflict to a shared subject took that to zero with coverage unchanged. Every test I had written asked "does it catch the false thing?" and none asked "does it leave the true thing alone?"

**Then the repair nearly made it worse.** With subject binding in place, the audit found five falsehoods that no fact contradicts. "The Starter plan costs 499 USD per seat per year" is refuted by nothing, because no Starter plan exists. Closing that gap means recording it as known-false, and that scored:

```
uncovered    5 -> 0        the repair worked
coverage     100% -> 100%  still looks perfect
autoimmunity 0% -> 81.8%   the vaccine was attacking the host
```

Recording "Starter plan costs 499" made it fuzzily match the **true** "Enterprise plan costs 499" on every word but one. A system reporting only coverage would have shipped that as a success. Applying subject binding one level deeper, to the vaccine itself, closed the holes with autoimmunity held at zero.

One number would have shown a perfect score twice.

There is a third thing the audit caught, on itself. My first hard negatives had the price written into them, and the company scenario supersedes that price from 499 to 549. Overnight, two of my labelled "true statements" became false ones, and the detector was correctly flagging them. The audit reported 71% precision and I went looking for a bug in the detector. There wasn't one. The labels had gone stale.

The fix was to read the quantities out of the facts in force instead of writing them down, which also means the audit survives a policy change without needing to be re-edited. Precision went back to 1.00. A benchmark that rots when your data changes is a benchmark that will lie to you later.

## The engineering that made it survive contact

A Cypher query that has never been executed is not a feature. FalkorDB implements a subset of openCypher, and my assumptions were wrong repeatedly, so the repository ships a verifier that seeds a fixture containing a cascade, a three-hop cycle, a contradiction cluster and embeddings, then executes every query and checks the shape of each result before commit. Eighteen queries, all executed.

Six bugs only appeared when the code ran. The two I would point at:

**A hidden write.** The cascade-containment update returned ids, so it read like a query and had been written with the read helper, which is not logged. It worked. Then a rebuild from the log silently lost it, and two claims reverted from `suspect` to `active`. There is now a structural test that fails if any write hides inside a read helper.

**The concurrency bug I would have shipped.** Reading `max(seq)` and adding one is a read-only query, and concurrent readers legitimately see the same value. With eight writers and two hundred writes the naive version produced 37 distinct sequence numbers. Allocating with a `SET`, so the database serialises it, gives 200 out of 200.

The graph is also treated as a projection rather than the source of truth. Every write is appended to a log before it is applied, and the rebuild is verified rather than assumed:

```
writes logged : 73
replayed      : 73 writes, 0 failed
identical     : True
```

Identical includes node ids. That is why the log stores whole statements rather than semantic events: replaying a statement reproduces its generated id exactly, whereas regenerating one would produce a different graph with every reference broken.

## Running it

FalkorDB is the only dependency beyond Python.

```bash
git clone https://github.com/TCPCore/immunity
cd immunity
docker compose up -d
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m immunegraph.verify_queries
.venv/bin/python -m pytest
.venv/bin/python scripts/verify_tracks.py
streamlit run app.py
```

`verify_queries` executes every Cypher query against a live instance. `verify_tracks.py` runs the capability each competition track is about and prints pass or fail against the graph, which is a stronger claim than naming tracks in a README.

## The numbers, including the uncomfortable one

Conflict detection against the bundled corpus, on a generated labelled set with hand-written true restatements as negatives:

```
precision   1.00     of every flag raised, all were genuinely false
recall      1.00     every generated contradiction was caught
f1          1.00
autoimmunity 0%      six hard negatives, phrased the way a person would say them
```

Then the part that decides the architecture. I benchmarked both paths at increasing graph size, against a local instance:

```
claims     fast path     detection sweep
   100       1.3 ms          1,118 ms
 1,000       1.3 ms         14,887 ms
```

The benchmark is deliberately hostile: every one of those claims is an unsupported root with no source, so the sweep has a finding to write for every row. Real graphs are not like that. But the shape is what matters and it does not change: **the fast path is flat and the sweep is not.**

That is not a defect, it is the reason the two exist. The thing you put in a request path has to be the flat one, so it is. The sweep is asynchronous, and at a thousand findings it is also the thing to batch, because the cost is dominated by one write per finding rather than by the query.

If you are building something similar, the lesson I would pass on is this: pick the quantity that should be a traversal and make it one, and measure both sides of your detector. I found a 20% failure in my own system only after I built the number nobody reports.

---

Built on [FalkorDB](https://www.falkordb.com/) for the WeMakeDevs and FalkorDB Graph Hacks hackathon, entered in all three tracks: Agents That Act on Connected Data, Agent Memory and Coordination, and Company Brain.

Code, graph model and every query: [github.com/TCPCore/immunity](https://github.com/TCPCore/immunity)
