# Your agent swarm is confidently wrong, and your memory layer cannot tell

*ImmuneGraph — WeMakeDevs × FalkorDB, Graph Hacks: Context for AI Agents (Oct 2026).
Entered in all three tracks. Code: [github.com/TCPCore/immunity](https://github.com/TCPCore/immunity).*

---

Everyone building multi-agent systems hits the same wall. Not a crash. Not a timeout. Something
worse: the swarm agrees on something false, and every individual step was valid.

Agent A says *"Mars has breathable oxygen at night."* Agent B, whose job is to reason, says *"then
we don't need helmets."* Agent C, whose job is to plan, says *"then we can skip life support."*
Three agents. Zero errors. Zero exceptions. A plan that kills the crew.

The standard answer — *"add a memory layer"* — does not address this. Memory layers are excellent
at answering **"what was said?"** They persist it, embed it, retrieve it, and hand it back with
impressive recall. None of them answer **"is this swarm getting epistemically sick?"**

What makes the failure expensive is that **the errors are correlated**. Three agents independently
hallucinating gives you three bad outputs, each detectable. One agent hallucinating while two derive
from it gives you **one** bad output that three agents agree on — and human reviewers use agreement
as a signal of truth. The swarm has manufactured consensus out of nothing.

## The reframe: memory should store support, not just content

The failure is not a storage problem. It is a *structure* problem. Agent B's claim is not wrong in
isolation — it is a valid inference. It is wrong because of what it points at.

So stop modelling claims as records and model them as a graph:

- a claim **is supported by** a source
- a claim **is derived from** another claim
- a claim **contradicts** another claim
- an agent **asserts** a claim

Once belief has that shape, the pathologies stop being vague and become *traversals*:

| Pathology | Graph shape |
| --- | --- |
| Self-justifying belief | a cycle in the support edges |
| Unsupported root | a claim with no source and no parent |
| Contradiction | a `CONTRADICTS` edge between two asserted claims |
| Cascade | reach along derivation edges back to a bad root |

That is the whole idea. Everything below is consequence.

## What is genuinely new here, and what is not

I am not going to claim this is unprecedented. It is not, and anyone who searches will find out.
Being precise about the boundary is worth more than a bold claim.

**Classic AI got here first.** Belief revision (AGM) and justification-based truth maintenance
(Doyle 1979; de Kleer 1986) are the direct ancestors of "track why a system believes something and
retract when the justification fails." [Beyond Prediction](https://arxiv.org/abs/2506.17331) argues
for exactly this discipline in AI systems — epistemic integrity, contradiction detection, truth
maintenance — as a theoretical blueprint.

**Contemporary research has the cascade insight.** [From Spark to Fire](https://arxiv.org/html/2603.04474)
models multi-agent collaboration as a **directed dependency graph**, identifies cascade amplification
and consensus inertia as intrinsic pathologies across six frameworks, and proposes a
**genealogy-graph governance layer** that suppresses propagation without altering the architecture —
raising defence success from 0.32 to above 0.89. If you take one thing from this post — that the
propagation structure is the thing worth modelling — they said it first.

**So what is actually different?**

| | What it does | What it leaves open |
| --- | --- | --- |
| Vector memory (Mem0, Zep, Letta) | stores and retrieves what was said | no support structure; contradictions live side by side |
| GraphRAG (Microsoft, Neo4j, FalkorDB SDK) | entity/community graph for better *retrieval* | the graph is a retrieval index, not a model of belief |
| Guardrails (NeMo, Guardrails AI) | validate one output against rules | pointwise; no notion of one agent deriving from another |
| Hallucination scoring (Galileo, Patronus, Vectara) | score a response against its retrieved context | no propagation; cannot tell "wrong" from "wrong and contagious" |
| Observability (LangSmith, Langfuse) | trace calls | shows what happened, not what was justified |
| Error-cascade research | models and **suppresses** propagation | suppression is not accumulation — the system never learns the truth |
| Truth maintenance (Doyle; Beyond Prediction) | belief revision, justifications | classic/theoretical; not a deployed multi-agent LLM system |

The contribution is three specific things, and I would rather state them narrowly than claim a
category:

**1. Two memories, not one.** Every system above tracks **one** thing. ImmuneGraph tracks two, of
opposite polarity: `Infection` and quarantined `Claim` nodes record **what was wrong**; a
`KernelFact` records **what is now held true**, written by the antibody that established it, citing
the source and the claim it refutes. Suppression alone leaves the swarm unable to answer "what
*should* we believe?" — which is the question a company actually needs answered.

**2. Immunity that compounds, with the split measured.** Corrected facts make future detection
*cheaper*, not just better. Detecting a cascade is a graph search that grows with the graph —
measured at **20.3 ms** for one sweep on a small graph. Checking a new claim against the kernel is a
comparison against a handful of facts — **1.31 ms median, 2.25 ms p95**. The ratio is roughly 15×,
and every correction moves work from the expensive path to the cheap one. Most systems treat
falsehood as a static anomaly to be filtered; this treats it as a flow whose handling cost *falls*
over time.

**3. It is a deployed system, not a benchmark result.** Query correctness verified against a live
instance, durability with a write-ahead log and a rebuild proven byte-identical, concurrency fixed
and measured, three working demos. The research says the idea is right. This says the idea runs.

I would rather be judged on the third than on the first two.

## How it was built

### The schema

Seven node types and eleven relationships in FalkorDB. The load-bearing parts:

```
(Agent)-[:ASSERTS]->(Claim)          who committed to what
(Claim)-[:DERIVED_FROM]->(Claim)     child -> parent, so cascades walk backwards
(Claim)-[:SUPPORTED_BY {quote}]->(Source)
(Infection)-[:TARGETS]->(Claim)      what is sick
(Infection)-[:NEUTRALIZES]-(Antibody)
(Antibody)-[:INJECTS]->(Claim)       the correction
(Antibody)-[:ESTABLISHES]->(KernelFact)
(Claim)-[:CONFLICTS_WITH]->(KernelFact)
```

`DERIVED_FROM` points child -> parent deliberately. It makes every cascade query a single backward
traversal: `(downstream)-[:DERIVED_FROM*1..5]->(root)`.

### Making the database load-bearing

Here is the trap I nearly fell into: a nice graph *visualisation* on top of ordinary storage. A
pretty node-link diagram proves nothing.

The fix was to define the product's core quantities **as graph queries**, so removing the database
removes the concepts themselves.

**Severity is graph reach.** How bad is an outbreak? Not a judgement call — it is how many claims
descend from the bad root and how many agents asserted them:

```cypher
MATCH (root:Claim {id: $root_claim_id})
OPTIONAL MATCH (down:Claim)-[:DERIVED_FROM*1..5]->(root)
WITH root, collect(DISTINCT down) AS downs
WITH root, downs, downs + [root] AS members
UNWIND members AS member
OPTIONAL MATCH (a:Agent)-[:ASSERTS]->(member)
RETURN root.id, size(downs) AS infected_claims, count(DISTINCT a) AS exposed_agents
```

Two contaminated claims and three exposed agents gives severity **6.0**. Delete the graph and
"severity" has no definition.

**Trust is derived, never stored.** The naive design decrements a counter when an agent is blamed.
Then it drifts, because you maintain the same fact in two places:

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

The second clause matters more than it looks. The Planner's trust drops to **0.85** even though it
never touched the false claim — it inherited the infection through the derivation chain. Trust that
is recomputed cannot desynchronise from the belief graph, because there is only one copy of truth.

### The two speeds

**Detection** finds lies the swarm has never seen. It is a graph search, so it gets slower as the
graph grows: **20.3 ms** for a full sweep locally, **234 ms** over a network.

**The kernel** recognises lies it already knows. It compares a new claim against a handful of facts:
**1.31 ms median**, **2.25 ms p95** locally.

```
SANITISER : contradicts kernel fact 'Mars has no breathable oxygen'
            overlap 0.67, decided in 1.3 ms — no model call, no cascade scan
```

That asymmetry is the design. Detection is *insurance*; the kernel is *immunity*. Anything that only
detects has to pay the expensive path forever.

### The design decision I would defend hardest

Immunity recall is the money shot: the same lie comes back and is caught instantly. My first version
had the LLM emit "this claim repeats that claim" edges.

That is a terrible idea and it took me a while to see why. The single most important moment in the
demo would depend on a non-deterministic model noticing a similarity. On stage. In front of judges.

So claims carry a deterministic `fingerprint` — normalised tokens, hashed — and recall is an
**indexed equality lookup**, not a judgement. When that misses (because "Mars has oxygen at night"
is not "Mars has breathable oxygen at night"), a vector index catches the paraphrase, and if vector
support is missing there is a token-overlap fallback whose *candidate set still comes from the
graph*. The model parses language; the database decides what the swarm believes.

## The three tracks

### Track 01 — Agents that act on connected data

Detection is the easy half. This track is about agents doing something with what the graph knows.

When the swarm finds an unsupported root it does not log a warning. An `Infection` opens and
`TARGETS` the claim; an `Antibody` is dispatched, `NEUTRALIZES` the infection and `INJECTS` a sourced
correction; the correction `CONTRADICTS` the original, which becomes `quarantined`; and
`contain_cascade` walks the derivation edges to invalidate everything that reasoned from it.

**The part I am most pleased with: anyone can submit a claim.** A scripted scenario proves the
mechanism works; it does not prove it works on anything you did not choose. So the swarm accepts
claims it has never seen, grounds them against Wikipedia — free, no API key, public data — and runs
the same machinery:

```
Visitor asserts: "Venus has a breathable atmosphere"
Sanitised against the kernel — no stored fact matches yet.
Public source consulted: Atmosphere of Venus.
Infection open on the claim — severity 2.0
Analyst reasons from it — severity 4.0
Planner reasons from it — severity 6.0
Antibody dispatched, citing the public source.
Quarantined — the cascade is contained.
```

**The propagation is real, not an animation.** The Analyst and Planner genuinely derive claims from
the visitor's statement, with `DERIVED_FROM` edges written to the database. The severity shown at
each step is read back from Q4 *after* the write — `1 + infected + exposed`, computed from edges that
exist — which is why it climbs 2 -> 4 -> 6 rather than stepping through fixed numbers.

Two decisions keep grounding honest. **The subject check:** Wikipedia's search matches individual
words, so "Mars is made of cheese" returns cheese articles, and my first version duly reported that
claim as **supported** — by an article about Cheez-Its. An article is now only considered if its
*title* shares a token with the claim. **Sentence-level coverage:** agreement needs one sentence to
contain essentially everything the claim asserts, because the overlap coefficient flatters short
claims. I also removed "only" from the limiting-word list after it refuted the *true* claim that the
Moon orbits the Earth.

Where it cannot decide, it returns **inconclusive**. A confidently wrong verdict would make the whole
demo theatre.

### Track 02 — Agent memory and coordination

Three kinds of memory, behaving differently. **Episodic** — `Message` and `Session`. **Semantic** —
`Claim`, `Source`, `Agent`, `KernelFact`. **Procedural** — `Antibody` and `Infection`: not *what*
happened but *what to do* when it happens again, including the recorded outcome, so effectiveness is
measurable rather than assumed.

And the memory outlives the database. FalkorDB Cloud's free tier has **no persistence and no
backups** — instances stop after a day idle and are deleted after a week. So every write is appended
to a log *before* it is applied, and the graph is a projection that can be rebuilt:

```
writes logged : 73
replayed      : 73 writes, 0 failed
identical     : True
```

"Identical" includes node ids, which is why the log stores whole statements rather than semantic
events. Events read better, but replay would have to reconstruct which write each implied — and
infection ids are UUIDs, so a replay that regenerated them would produce a different graph with every
reference broken. Statements cannot diverge; replaying them *is* the original write.

### Track 03 — Company brain

A company's characteristic failure is not retrieval failure. It is that one agent's confident wrong
**price** gets repeated by another department until it becomes doctrine. Four synthetic documents load
as `Source` nodes, and the facts they establish become `KernelFact`s linked to them:

```
Loaded 4 company documents into the kernel as 5 facts.
Sales: "Enterprise is 399 USD per seat per year" — conflict found in 66.6ms.
Support repeats it — the wrong price is now spreading.
Infection opened across two departments — severity 4.0.
Quarantined both. The antibody cites Enterprise and Team pricing (2026).
Finance raises the price to 549 USD. The old fact is SUPERSEDED, not deleted.
Sales quotes the old 499 price — correct last week, wrong now. Caught in 89.7ms.
```

Two things a company brain needs that Mars did not.

**Quantity conflicts.** Everything a company states as fact is a number — price, SLA, retention
window, seat count. My conflict rule was negation-based, and *"399 per seat" does not negate "499 per
seat."* The two agree on every word except the one that matters, so the sanitiser scored the wrong
price as **aligned** with the pricing policy and blessed it as doctrine.

That is the worst possible bug in a system built to catch confident wrongness: confidently wrong
itself. `conflicts_on_numbers` now requires both sides to state a number, the numbers to differ, and
the non-numeric context to be the same subject — so a refund window and an incident SLA never
conflict just because both contain digits.

**Policy drift.** Facts are versioned. A price change supersedes the old fact rather than deleting
it, and every active claim is re-judged against the new truth. A brain that cannot forget is as
dangerous as one that cannot learn.

## Six bugs that only running the code found

**A Cypher query that has never been executed is not a feature.** FalkorDB implements a subset of
openCypher, and my assumptions were wrong repeatedly. So I wrote a verifier that seeds a fixture
containing a cascade, a 3-hop cycle, a contradiction cluster and embeddings, then executes every
query and checks the *shape* of each result before commit. Eighteen queries, all executed.

1. **`ORDER BY score DESC` was backwards.** The vector index yields a cosine **distance**
   (0.0 = identical) despite the option being named `similarityFunction`. My query returned the
   *least* similar candidates — silent at small scale, fatal at large.
2. The query vector **must** be wrapped in `vecf32(...)`; a bare list raises
   `Invalid arguments for procedure`.
3. **A false-positive contradiction.** "If X, we don't need helmets" was detected as *contradicting*
   X, because lexical negation cannot tell "the premise implies a negation" from "this negates the
   premise." It produced an outbreak rooted at the wrong claim.
4. **`count(*)` under `OPTIONAL MATCH` counts rows, not edges.** An empty kernel reported a conflict
   that did not exist — in exactly the direction that flattered the project.
5. **A hidden write.** Cascade containment returned ids, so it *read* like a query and had been
   written with the read helper — which is not logged. It worked, and a rebuild silently lost it. Two
   claims reverted from `suspect` to `active`. There is now a structural test that fails if any write
   hides inside a read helper.
6. **The concurrency bug I would have shipped.** `max(seq) + 1` is a read-only query; concurrent
   readers legitimately see the same value. With 8 writers and 200 writes the naive version produced
   **37 distinct sequence numbers** (163 collisions). Allocating with a `SET` gives 200/200.

Number 5 is my favourite, because the fingerprint caught it — the rebuild *looked* fine and was not.
Number 6 is the one a reviewer would never catch by reading.

## The metric nobody reports

Every system of this kind reports **coverage**: how much it catches. That number is gameable — a
detector that flags everything scores 100% and is worthless. So I built the other half and pointed
it at my own code.

The [Agent-Native Immune System](https://arxiv.org/abs/2606.28270) paper names this missing metric
— the **Autoimmunity Rate**, the false-positive intervention rate — among the field's open
challenges. It does not define or compute it. Here it is, paired with coverage so neither can be
optimised alone:

```
COVERAGE       how many generated falsehoods the defence catches
AUTOIMMUNITY   how many TRUE statements it wrongly flags      <- nobody reports this
ATTRIBUTION    did it fire at the RIGHT fact, or just at A fact
```

The probes are generated from the system's own kernel: perturbed numbers, transplanted numbers,
substituted entities, and truth-preserving paraphrases that must **not** fire. No labelled dataset
required — the knowledge base is the test set.

### What it found, with 154 tests passing

**The defence was subject-blind.** `"Enterprise costs 499 USD per seat per year"` is true. It was
judged to **conflict**, because it numerically mismatches the *Team* plan's fact and the rule never
checked which entity the claim was about.

```
AUTOIMMUNITY RATE: 2/10 = 20% of true statements wrongly flagged
```

Binding a conflict to a shared subject took that to **0% with coverage unchanged at 100%**. A bug
invisible to every test I had written, because my tests asked "does it catch the false thing?" and
never "does it leave the true thing alone?".

**Then the repair nearly made it worse.** With subject binding in place, the audit found five
falsehoods that no fact contradicts — `"The Starter plan costs 499 USD per seat per year"` is not
contradicted by anything, because Halcyon sells no Starter plan. Closing that gap means recording
it as known-false. Which scored:

```
uncovered   : 5 -> 0          the repair worked
coverage    : 100% -> 100%    still looks perfect
autoimmunity: 0% -> 81.8%     the vaccine was attacking the host
```

Recording "Starter plan costs 499" made it fuzzily match the **true** "Enterprise plan costs 499"
on every word but one. **A system reporting only coverage would have shipped that as a success.**
Applying subject binding one level deeper — to the vaccine itself — closed the holes with
autoimmunity held at zero.

That is the whole argument for a two-sided metric in one sequence. One number would have shown a
perfect score twice.

### Why this is the part I would defend

Detection is the published part; I have said so. But a defence that cannot measure its own
false-positive rate is not deployable in a company brain, because blocking a **correct** answer has
a real cost — a customer told the wrong price by a system "protecting" them from a true sentence —
and that cost is invisible in every metric currently reported.

It also turns the demo's claim from "trust us" into "run it": the probes come from your own
knowledge base, so the number is about *your* corpus.

## Where this is useful, measured

| Claim | Measured |
| --- | --- |
| Every Cypher query executes, expected shape | **18/18**, local and cloud |
| Kernel fast path | **1.31 ms** median / 2.25 ms p95 local · 21 ms cloud |
| Full detection sweep | **20.3 ms** local · 234 ms cloud |
| Real policy conflict | **7.2 ms** local · 97 ms cloud |
| Rebuild from log | **73 writes, 0 failed, identical** |
| Concurrent sequence allocation | **200/200** under 8 writers |
| Regression suite | **154 tests** |

The local-vs-cloud gap is **pure network round-trips** — same code, same query, 16× slower. So the
compute is cheap and the *topology* is what you optimise.

That produces the architecture: the **1.3 ms** kernel check belongs inline, blocking bad answers
before a customer sees them; the **20 ms** detection sweep belongs asynchronous, producing a
severity-ranked review queue. One sweep in the hot path would be the wrong system.

It applies first where **output is acted on and a wrong answer costs more than the check**:
customer-facing agent fleets (few, stable, written-down facts); multi-agent analysis pipelines
(severity as a triage queue — review three cascades, not ninety outputs); regulated domains
(provenance as a traversal, `supersede` for "what did we believe that day"); and RAG output
verification (the answer that cites a source and contradicts it). Not worth it for low-stakes,
high-volume work with **no written ground truth** — the kernel would have nothing to hold.

## What I am not claiming

- **The core idea is not mine.** Dependency-graph cascade modelling and genealogy-based governance
  are published; truth maintenance dates to 1979. The contribution is the dual memory, the measured
  two-speed split, and that it runs.
- The antibody is a model and can be wrong. It is constrained to cite a source, record its outcome,
  and make a bad correction visible. This is a **containment system, not a truth oracle.**
- **No labelled evaluation set.** There is no precision/recall figure for conflict detection. The
  tests assert behaviour on hand-written cases, not accuracy on a corpus.
- **Matching is lexical.** A contradiction in different vocabulary is missed; the system says
  "inconclusive" rather than guessing.
- **Scale is untested.** Every number above is on a graph of tens of nodes.
- **Durability is within a session.** True cross-restart durability needs durable storage.

Saying that out loud is worth more than another feature.

## If you build something similar

- **Pick the quantity that should be a traversal, and make it one.** If you can compute your
  product's core metric without the database, you do not have a graph product.
- **Never let a model decide something your demo depends on.** Model for language, graph for belief.
- **Execute every query and assert on the results.** Documentation is not verification.
- **Track what is true, not only what is wrong.** Detection tells you the swarm is sick; only a
  kernel tells it what to believe instead.
- **Write down what you are not claiming.** The scope you draw honestly is part of the design.

---

*Built on [FalkorDB](https://www.falkordb.com/). 46 commits, 154 tests, 18 queries verified against
a live instance. Graph model and every query: [github.com/TCPCore/immunity](https://github.com/TCPCore/immunity).*
