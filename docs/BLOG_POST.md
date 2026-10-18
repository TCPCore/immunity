# I built an immune system for my agent swarm, then audited it and found it was wrong 20% of the time

If you run multi-agent systems, you have probably hit this and blamed something else.

Agent A says "Mars has breathable oxygen at night." Agent B, whose job is to reason, says "then we don't need helmets." Agent C, whose job is to plan, says "then we can skip life support."

Three agents. No exceptions. No errors. Every inference valid. A plan that kills the crew.

The failure is not that an LLM hallucinated. That happens, and we have tools for it. The failure is that **the swarm manufactured consensus**. One agent invented something, two others derived from it correctly, and now three agents agree. And agreement is exactly what we use as a signal of truth.

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

Same principle for trust. A naive design decrements a counter when an agent gets blamed. Then it drifts, because you now maintain the same fact in two places. Instead trust is recomputed from exposure, and the second clause below is what matters:

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

You can assert an infected claim, or you can assert something derived from one. In the demo the Planner drops to 0.85 trust despite never touching the false claim, because it inherited the infection through the chain.

## Two memories, not one

This is the design decision I would defend hardest, and it is where my approach differs from most.

Systems in this space track **one** thing: what went wrong. A blocklist, an alert, a flagged record.

That leaves the swarm unable to answer the next question. Fine, you caught the lie. What should it believe instead?

So there are two memories with opposite polarity. `Infection` and quarantined `Claim` nodes record what was **wrong**. A `KernelFact` records what is now held **true**, written by the antibody that established it, citing the source and the claim it refutes.

The payoff is speed, and it is measurable. Detecting a cascade is a graph search that grows with the graph. Checking a new claim against the kernel is a comparison against a handful of facts. Against a local FalkorDB instance:

```
kernel fast path   1.31 ms median, 2.25 ms p95
detection sweep    20.3 ms
```

Roughly 15x, and every correction moves work from the expensive path to the cheap one. Detection is insurance. The kernel is immunity. A system that only detects pays the expensive path forever.

## Making it real: three things a judge can click

I did not want a diagram of a concept. Three working paths:

**1. The scripted cascade.** Three agents plan a Mars mission. The first asserts something with no source. The other two reason correctly. The swarm detects the unsupported root, opens an infection, dispatches an antibody citing NASA, quarantines the claim, and invalidates the two derivations. Severity 6.0.

**2. Anyone can submit a claim.** This matters, because a scripted demo only proves the mechanism works on inputs you chose. You type a statement, it is grounded against Wikipedia (free, no API key, public data), and then the same machinery runs. The infection genuinely spreads through the Analyst and Planner, because those agents really do derive claims from yours, with real `DERIVED_FROM` edges. The severity climbs 2.0 to 4.0 to 6.0 because it is recomputed from the graph after each write, not because a script says so.

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

Getting this honest took two corrections. Wikipedia's search matches individual words, so "Mars is made of cheese" returned articles about cheese, and my first version reported that claim as **supported** by an article about Cheez-Its. Articles are now only considered if the title shares a token with the claim. And agreement requires a single sentence to contain essentially everything the claim asserts, because the overlap coefficient flatters short claims.

**3. A company brain.** Four synthetic documents load as sources, and the facts they establish become kernel facts linked to them. A sales agent invents a price, support repeats it with a real derivation edge, and the infection is contained by an antibody citing the pricing document. Then the price actually changes, the old fact is superseded, and the answer that was correct last week gets flagged.

## The part that is genuinely new, and the part that is not

I am not going to claim this idea is unprecedented. It is not.

Truth maintenance and belief revision are from 1979. The insight that multi-agent LLM collaboration should be modelled as a directed dependency graph, with cascades as an intrinsic pathology and a governance layer to suppress them, is published and measured over six frameworks. The immune-system metaphor for agents, including a vaccine taxonomy, is published too.

What I think is actually new is narrower: **how you know your detector is any good.**

Every system in this space reports coverage, meaning how much it catches. That number is gameable. A detector that flags every statement scores 100% and is useless.

The other side has a name. One recent paper lists the **Autoimmunity Rate**, the false-positive intervention rate, among the field's open challenges. It does not define, compute, or use it. I could not find anyone who does.

So I built it, paired it with coverage, and added a third measure: did the defence fire at the right fact, or merely at one. Then I pointed all three at my own code.

```
COVERAGE       generated falsehoods the defence catches
AUTOIMMUNITY   TRUE statements it wrongly flags
ATTRIBUTION    did it fire at the RIGHT fact
```

The probes are generated from the system's own knowledge base: perturbed numbers, transplanted numbers, substituted entities, and truth-preserving paraphrases that must not fire. No labelled dataset needed. Your knowledge base is the test set.

## What the audit found, with 134 tests passing

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

That sequence is the whole argument for measuring both sides. One number would have shown a perfect score twice.

## What I am not claiming

This is the part that matters more than the feature list.

The core idea is not mine. The dependency-graph cascade work is published, and truth maintenance is decades old.

The antibody is a model and can be wrong. It is constrained to cite a source, record its outcome, and make a bad correction visible in the graph. This is a containment system, not a truth oracle.

There is no labelled evaluation set, so there is no precision or recall figure for conflict detection. The tests assert behaviour on hand-written cases, not accuracy on a corpus.

Matching is lexical. A contradiction phrased in different vocabulary is missed, and the system says "inconclusive" rather than guessing.

Scale is untested. Every number in this post is from a graph of tens of nodes.

Durability is within a session. The write-ahead log protects against a graph wipe; true cross-restart durability needs durable storage.

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

`verify_queries` executes every Cypher query against a live instance and checks the shape of each result before commit. That exists because a query which has never run is not a feature: six of the bugs I hit were invisible in the source and only appeared when executed.

`verify_tracks.py` runs the capability each competition track is about and prints pass or fail against the graph, which is a stronger claim than naming the tracks in a README.

## If you are building something similar

Pick the quantity that should be a traversal, and make it one. If you can compute your product's core metric without the database, you do not have a graph product.

Never let a model decide something your demo depends on. Use the model for language, the graph for belief.

Execute every query and assert on the results. Documentation is not verification.

Track what is true, not only what is wrong. Detection tells you the swarm is sick. Only a kernel tells it what to believe instead.

And measure both sides of your detector. I found a 20% failure in my own system only after I built the number nobody reports.

---

Built on [FalkorDB](https://www.falkordb.com/) for the WeMakeDevs and FalkorDB Graph Hacks hackathon, entered in all three tracks: Agents That Act on Connected Data, Agent Memory and Coordination, and Company Brain.

Code, graph model and every query: [github.com/TCPCore/immunity](https://github.com/TCPCore/immunity)
