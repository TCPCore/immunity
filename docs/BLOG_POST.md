# Blog post — ImmuneGraph

For publishing on dev.to / Hashnode / your own site. Structured around the three tracks of
Graph Hacks, all three of which this project enters, with a section of evidence for each.

> **Note on prizes.** The current rules (rule 14) list three track prizes and no separate prize
> for blogging. Publish this because it is the clearest explanation of the work and judges read
> it, not because there is a side prize for it.

---

## Your agent swarm is confidently wrong, and your memory layer cannot tell

Everyone building multi-agent systems hits the same wall eventually. Not a crash. Not a timeout.
Something worse: the swarm agrees on something false, and every agent is behaving perfectly.

Here is the shape of it. Agent A says *"Mars has breathable oxygen at night."* Agent B, whose job
is to reason, says *"then we don't need helmets."* Agent C, whose job is to plan, says *"then we
can skip life support."* Three agents. Zero errors. Zero exceptions. A plan that kills the crew.

I built a demo around this because the standard answer — *"add a memory layer"* — does not
address it. Memory layers are extremely good at answering **"what was said?"** They persist it,
embed it, retrieve it, and hand it back with impressive recall.

None of them answer **"is this swarm getting epistemically sick?"**

### The reframe: memory should store support, not just content

The failure is not a storage problem, it is a *structure* problem. Agent B's claim is not wrong
in isolation — it is a valid inference. It is wrong because of what it points at.

So I stopped modelling claims as records and started modelling them as a graph:

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
| Cascade | reachability along derivation edges back to a bad root |

That is the whole idea. Everything below is consequence.

---

## Track 02 — Agent Memory and Coordination

This track asks what an agent *remembers*, and how agents stay in step. The interesting answer
turned out to be that a swarm needs three kinds of memory, and they do not behave alike.

**Episodic** — `Message` and `Session` nodes. What was said, in what order.

**Semantic** — `Claim`, `Source`, `Agent`, and `KernelFact`. What is believed, on what authority.

**Procedural** — `Antibody` and `Infection`. Not *what* happened but *what to do* when it happens
again. This is the memory that makes the system immune rather than merely observant: an
`Antibody` records the correction *and its outcome*, so effectiveness is measurable rather than
assumed.

**Trust is derived, never stored.** The naive design decrements a trust counter in Python when an
agent is blamed. Then it drifts, because you now maintain the same fact in two places. Instead:

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

Two kinds of exposure: asserting an infected claim, or asserting something *derived from* one.
That second clause matters more than it looks. The Planner's trust drops to **0.85** even though
it never touched the false claim — it inherited the infection through the chain. Trust that is
recomputed cannot desynchronise from the belief graph, because there is only one copy of the
truth.

**The swarm keeps a kernel of truth.** The hardest thing to explain about this system is that it
has two opposite memories. `Infection` and quarantined `Claim` nodes record what was **wrong**.
A `KernelFact` records what is now held to be **true** — written by the antibody that established
it, citing the source and the claim it refutes:

```
(Claim)<-[:CONFLICTS_WITH]-(KernelFact)-[:ESTABLISHED_BY]->(Antibody)
(KernelFact)-[:CITES]->(Source)
```

That distinction is what makes immunity *fast*. Detecting a cascade is a graph search that gets
more expensive as the graph grows — about **3.1 seconds** in the demo. Checking a new claim
against the kernel is a comparison against a handful of known facts:

```
SANITISER : contradicts kernel fact 'Mars has no breathable oxygen'
            overlap 0.67, decided in 4.6ms — no model call, no cascade scan
```

Detection finds lies it has never seen and gets slower. The kernel recognises lies it already
knows and does not. A correct immune system needs both.

**And the memory survives the database.** FalkorDB Cloud's free tier has no persistence and no
backups — instances stop after a day idle and are deleted after seven. A kernel of truth that
evaporates is not much of a truth. So every write is appended to a log *before* it is applied,
and the graph is treated as a projection that can be rebuilt:

```
writes logged : 73
replayed      : 73 writes, 0 failed
identical     : True
```

"Identical" includes node ids, which is why the log stores whole statements rather than semantic
events. Events read better, but replay would have to reconstruct which write each implied — and
infection ids are UUIDs, so a replay that regenerated them would produce a different graph with
every reference broken. Statements cannot diverge; replaying them *is* the original write.

---

## Track 01 — Agents That Act on Connected Data

Detection is the easy half. This track is about agents doing something with what the graph knows.

When the swarm finds an unsupported root it does not log a warning. It **acts**:

1. An `Infection` opens, `TARGETS` the claim, and its severity is *defined* as the traversal.
2. An `Antibody` is dispatched, which `NEUTRALIZES` the infection and `INJECTS` a sourced
   correction.
3. The correction `CONTRADICTS` the original, whose status becomes `quarantined`.
4. `contain_cascade` walks the derivation edges and invalidates every claim that reasoned from it.

**Severity is graph reach, not a judgement call:**

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
"severity" has no definition — which is rule 3's actual test.

### The part I am most pleased with: anyone can submit a claim

A scripted scenario proves the mechanism works. It does not prove it works on anything you did
not choose. So the swarm accepts claims it has never seen and grounds them against **Wikipedia** —
free, no API key, and unambiguously public data per rule 6 — then runs the *same* machinery:

```
🗣️  Visitor asserts: "Venus has a breathable atmosphere"
🔎  Sanitised against the kernel — no stored fact matches yet.
🌐  Public source consulted: Atmosphere of Venus.
🦠  Infection open on the claim — severity 2.0
↗️  Analyst reasons from it — severity 4.0
↗️  Planner reasons from it — severity 6.0
🛡️  Antibody dispatched, citing the public source.
✅  Quarantined — the cascade is contained.
```

**The propagation is real, not an animation.** The Analyst and Planner genuinely derive claims
from the visitor's statement, with `DERIVED_FROM` edges written to the database. The severity
shown at each step is read back from Q4 *after* the write — `1 + infected + exposed` computed
from edges that exist — which is why it climbs 2 → 4 → 6 rather than stepping through fixed
numbers. Containment is `contain_cascade` over that real structure.

### Two decisions that keep grounding honest

**The subject check.** Wikipedia's search matches individual *words*. Asking about "Mars is made
of cheese" returns articles about cheese, and my first version duly reported that claim as
**supported** — by an article about Cheez-Its. An article is now only considered if its *title*
shares a token with the claim. No cheese article is called anything with "Mars" in it, so that
claim comes back **inconclusive**.

**Sentence-level coverage.** Judging a whole article fires on unrelated text, and the overlap
coefficient flatters short claims — two shared words out of three reads as 0.67. Agreement now
requires a single sentence to contain essentially everything the claim asserts. I also removed
"only" from the limiting-word list after it refuted the *true* claim that the Moon orbits the
Earth.

Where it cannot decide, it says so. A confidently wrong verdict would make the whole demo
theatre.

---

## Track 03 — Company Brain

This started as a generalisation I was not going to claim. Then I built it, because the claim
only becomes honest if the artefact exists.

A company brain's characteristic failure is **not** retrieval failure. It is that one agent's
confident wrong answer gets repeated by another department until it becomes doctrine. A wrong
price is a million-dollar hallucination, not an embarrassing paragraph.

So: four synthetic documents ([`data/company/`](../data/company/)) load as `Source` nodes, and
the facts they establish become `KernelFact`s linked to them. Provenance is a traversal, not a
citation a model wrote from memory. Then the scenario runs:

```
📚  Loaded 4 company documents into the kernel as 5 facts.
⚖️  Sales answers 'What does the Enterprise plan cost?' with
    'The Enterprise plan is 399 USD per seat per year.' — conflict in 66.6ms.
↗️  Support repeats it to a customer — the wrong price is now spreading.
🦠  Infection opened across two departments — severity 4.0.
🛡️  Quarantined both. The antibody cites Enterprise and Team pricing (2026).
📝  Finance raises the price to 549 USD. The old fact is SUPERSEDED, not deleted.
⏳  Sales quotes the old 499 price — correct last week, wrong now. Caught in 89.7ms.
```

Support is not independently wrong — it is repeating a colleague, so its claim carries a real
`DERIVED_FROM` edge back to Sales. That is what makes it a cascade rather than two coincidences,
and it is why severity is 4.0 rather than 1.0.

### Two things a company brain needs that Mars did not

**Quantity conflicts.** Everything a company states as fact is a number — price, SLA, retention
window, seat count. My existing conflict rule was negation-based: does one side say "not" and the
other not. But *"Enterprise is 399 per seat"* does not negate *"Enterprise costs 499 per seat"*.
The two agree on every word except the only one that matters, so the sanitiser scored the wrong
price as **aligned** with the pricing policy and blessed it as doctrine.

That is the worst class of bug this project can have: a system built to catch confident wrongness,
confidently wrong itself. `conflicts_on_numbers` now requires both sides to state a number, the
numbers to differ, and the non-numeric context to be the same subject — so a refund window and an
incident SLA never conflict just because both contain digits.

**Policy drift.** Facts are versioned. When the price changes, the old fact is `superseded` and
kept rather than deleted, so "when did we stop believing this" stays answerable. Every claim still
active is then re-judged against the new truth, and the answer that was correct last week is
flagged. A brain that cannot forget is as dangerous as one that cannot learn.

The corpus is synthetic, so rule 6 is satisfied by construction.

## Five bugs that only running the code found

This is the part I would tell anyone building on a graph database to take seriously: **a Cypher
query that has never been executed is not a feature.** FalkorDB implements a subset of openCypher,
and my assumptions were wrong repeatedly. So I wrote a verifier that seeds a fixture containing a
cascade, a 3-hop cycle, a contradiction cluster and embeddings, then executes every query and
checks the *shape* of each result before commit.

What it caught:

1. **`ORDER BY score DESC` was backwards.** The vector index yields a cosine **distance**
   (0.0 = identical) even though the option is named `similarityFunction`. My query was returning
   the *least* similar candidates — a silent correctness bug that only appears at scale.
2. The query vector **must** be wrapped in `vecf32(...)`. A bare list raises
   `Invalid arguments for procedure`.
3. **A false-positive contradiction.** "If X, we don't need helmets" was detected as
   *contradicting* X, because lexical negation cannot tell "the premise implies a negation" from
   "this negates the premise". It produced an outbreak rooted at the wrong claim.
4. **`count(*)` under `OPTIONAL MATCH` counts rows, not edges.** An empty kernel reported one
   conflict that did not exist — in exactly the direction that flattered the project. Named
   relationship counts ignore nulls; `count(*)` does not.
5. **A hidden write.** The cascade-containment update returned ids, so it *read* like a query and
   had been written with the read helper — which is not logged. It worked, and a rebuild from the
   log silently lost it. Two claims reverted from `suspect` to `active`. There is now a
   structural test that fails if any write hides inside a read helper.
6. **The concurrency bug I would have shipped.** Reading `max(seq)` and adding one is a
   *read-only* query — concurrent readers legitimately see the same value. With 8 concurrent
   writers and 200 writes, the naive version produced **37 distinct sequence numbers** (163
   collisions). Allocating with a `SET`, so the database serialises it, gives 200/200.

---

## What I am not claiming

- The antibody is a model, and a model can be wrong. It is constrained to cite a source, record
  its outcome, and make a bad correction visible in the graph. This is a **containment system,
  not a truth oracle.**
- The durability log protects against a graph wipe *within* a session. True cross-restart
  durability needs the log on durable storage or a paid FalkorDB tier.
- Grounding is lexical. It cannot refute a claim whose contradiction is phrased in entirely
  different vocabulary, and it returns "inconclusive" instead of guessing.

Saying that out loud is worth more than another feature.

---

## If you are building something similar

- **Pick the quantity that should be a traversal, and make it one.** If you can compute your
  product's core metric without the database, you do not have a graph product.
- **Never let a model decide something the demo depends on.** Use the model for language, the
  graph for belief.
- **Execute every query and assert on the results.** Documentation is not verification. Most of
  the bugs above were invisible in the source.
- **Write down what you are not claiming.** The scope you draw honestly is part of the design.

---

*ImmuneGraph is built on [FalkorDB](https://www.falkordb.com/) for the WeMakeDevs × FalkorDB
**Graph Hacks: Context for AI Agents** hackathon (Oct 15–18, 2026), entered in all three tracks:
**01 — Agents That Act on Connected Data**, **02 — Agent Memory and Coordination**, and
**03 — Company Brain**.*

Code, graph model and every Cypher query: [github.com/TCPCore/immunity](https://github.com/TCPCore/immunity).
134 tests, all of which run against a live instance.*
