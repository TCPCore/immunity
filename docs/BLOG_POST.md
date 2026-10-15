# Blog post draft — ImmuneGraph

For the **Best blog post** side prize (AirPods 5). Publish on dev.to / Hashnode / your own
site, then link it in the submission. Edit the voice to sound like you before publishing.

---

## Your agent swarm is confidently wrong, and your memory layer can't tell

Everyone building multi-agent systems hits the same wall eventually. Not a crash. Not a
timeout. Something worse: the swarm agrees on something false, and every agent is behaving
perfectly.

Here is the shape of it. Agent A says *"Mars has breathable oxygen at night."* Agent B, whose
job is to reason, says *"then we don't need helmets."* Agent C, whose job is to plan, says
*"then we can skip life support."* Three agents. Zero errors. Zero exceptions. A plan that
kills the crew.

I built a demo around this because I kept noticing that the standard answer — *"add a memory
layer"* — does not address it. Memory layers are extremely good at answering **"what was
said?"** They persist it, embed it, retrieve it, and hand it back with impressive recall.

None of them answer **"is this swarm getting epistemically sick?"**

### The reframe: memory should store support, not just content

The failure is not a storage problem, it is a *structure* problem. Agent B's claim is not
wrong in isolation — it is a valid inference. It is wrong because of what it points at.

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

### Making the database load-bearing

Here is the trap I nearly fell into: building a nice graph *visualisation* on top of ordinary
storage. A pretty node-link diagram proves nothing.

The fix was to define the product's core quantities **as graph queries**, so that removing the
database removes the concepts themselves.

**Severity is graph reach.** How bad is an outbreak? Not a judgement call — it's how many
claims descend from the bad root and how many agents asserted them:

```cypher
MATCH (root:Claim {id: $root_claim_id})
OPTIONAL MATCH (down:Claim)-[:DERIVED_FROM*1..5]->(root)
WITH root, collect(DISTINCT down) AS downs
WITH root, downs, downs + [root] AS members
UNWIND members AS member
OPTIONAL MATCH (a:Agent)-[:ASSERTS]->(member)
RETURN root.id, size(downs) AS infected_claims, count(DISTINCT a) AS exposed_agents
```

In the demo that returns 2 contaminated claims and 3 exposed agents, so severity is 6. Delete
the graph and "severity" has no definition.

**Trust is derived, never stored.** The naive design decrements a trust counter in Python when
an agent is blamed. Then it drifts, because now you maintain the same fact in two places. Instead:

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
That second clause matters more than it looks. In the demo the Planner's trust drops to 0.85
even though it never touched the false claim — it inherited the infection through the chain.
Trust that is recomputed can't desynchronise from the belief graph, because there is only one
copy of the truth.

### The design decision I'd defend hardest

Immunity recall is the money shot: the same lie comes back and gets caught instantly. My first
version had the LLM emit "this claim repeats that claim" edges.

That is a terrible idea and it took me a while to see why. The single most important moment in
the demo would depend on a non-deterministic model noticing a similarity. On stage. In front of
judges.

So claims now carry a deterministic `fingerprint` — normalised tokens, hashed — and recall is an
**indexed equality lookup**, not a judgement. When that misses (because "Mars has oxygen at
night" is not "Mars has breathable oxygen at night"), a vector index catches the paraphrase,
and if vector support is missing there's a token-overlap fallback whose *candidate set still
comes from the graph*. The model parses language; the database decides what the swarm believes.

### Five bugs that only running the code found

This is the part I'd tell anyone building on a graph database to take seriously: **a Cypher
query that has never been executed is not a feature.** FalkorDB implements a subset of
openCypher, and my assumptions were wrong repeatedly. I wrote a verifier that seeds a fixture
containing a cascade, a 3-hop cycle, a contradiction cluster and embeddings, then executes
every query and checks the *shape* of each result.

What it caught:

1. **`ORDER BY score DESC` was backwards.** The vector index yields a cosine **distance**
   (0.0 = identical) even though the option is named `similarityFunction`. My query was
   returning the *least* similar candidates. With fewer claims than `k` you would never notice
   — it's a silent correctness bug that only appears at scale.
2. The query vector **must** be wrapped in `vecf32(...)`. A bare list raises `Invalid arguments
   for procedure`.
3. **A false-positive contradiction.** "If X, we don't need helmets" was detected as
   *contradicting* X, because lexical negation can't tell "the premise implies a negation"
   from "this negates the premise". It produced an outbreak rooted at the wrong claim. Fix:
   derivation and contradiction are mutually exclusive for the same pair of claims.
4. **Case-sensitive source patterns** silently dropped every citation phrased "According to …".
5. **The concurrency bug I'd have shipped.** Reading `max(seq)` and adding one is a *read-only*
   query — concurrent readers legitimately see the same value. With 8 concurrent writers and
   200 writes, the naive version produced **37 distinct sequence numbers** (163 collisions).
   Allocating with a `SET` instead, so the database serialises it, gives 200/200.

Number 5 is my favourite because it's the one a reviewer would never catch by reading, and it
was sitting in the code path that Track 02 explicitly asks about.

### What I'd tell you if you're building something similar

- **Pick the quantity that should be a traversal, and make it one.** If you can compute your
  product's core metric without the database, you don't have a graph product.
- **Never let a model decide something the demo depends on.** Use the model for language, the
  graph for belief.
- **Execute every query and assert on the results.** Documentation is not verification. Four of
  the five bugs above were invisible in the source.
- **Write down what you're not claiming.** The antibody is an LLM and can be wrong. We constrain
  it to cite a source, record its outcome, and make a bad correction visible in the graph — but
  this is a containment system, not a truth oracle. Saying that out loud is worth more than
  another feature.

---

*ImmuneGraph is built on [FalkorDB](https://www.falkordb.com/) for the WeMakeDevs × FalkorDB
"Graph Hacks: Context for AI Agents" hackathon, entered in Track 01 (Agents That Act on
Connected Data) and Track 02 (Agent Memory and Coordination). Code, graph model and the full
set of Cypher queries: [github.com/TCPCore/immunity](https://github.com/TCPCore/immunity).*
