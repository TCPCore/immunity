# ImmuneGraph, Graph Data Model

> This document is the authoritative schema. Every Cypher query in the product is
> written against this model. If you change the model, update this file in the same commit.

FalkorDB is not a storage layer here. The detection algorithms **are** Cypher queries,
and the immune response is a graph write. Remove FalkorDB and there is no product.

Dialect note: FalkorDB implements a **subset of openCypher v9**. Known constraints that
shaped this model are listed at the bottom, read them before writing new queries.

---

## 1. Memory taxonomy (Track 02 alignment)

The three memory types the track asks for are modelled as distinct node families:

| Memory type | Nodes | Why |
| --- | --- | --- |
| **Episodic** | `Message`, `Session` | Raw, timestamped, ordered utterances. Never mutated. This is the permanent conversation history. |
| **Semantic** | `Claim`, `Source`, `Agent` | Durable facts/beliefs and who holds them. Beliefs change: `Claim.status` moves over time. |
| **Procedural** | `Antibody`, `Infection` | *How the swarm responds* to a class of epistemic failure. Reused on later outbreaks. |

Shared state is the graph itself: every agent reads and writes the same
`Session` subgraph, so coordination needs no message bus.
Multi-tenancy is handled by FalkorDB's multi-graph feature, one graph per tenant
(`select_graph(f"tenant_{tenant_id}")`), so isolation is physical as well as logical.

---

## 2. Nodes

Nine node labels in use: `Session`, `Message`, `Agent`, `Claim`, `Source`, `Infection`, `Antibody`, `KernelFact`, `RefutedStatement`.

`RefutedStatement` is negative memory, added after the self-audit showed that some falsehoods are unreachable by contradiction. `Tenant` is declared in `schema.py` for validation but is never instantiated: tenancy is the `tenant_id` property on `Agent`.

```cypher
// --- identity & episode (episodic memory) ---
(:Tenant   {id, name, created_at})
(:Session  {id, tenant_id, topic, started_at, ended_at, status})
(:Message  {id, session_id, seq, text, ts, role})
(:Agent    {id, tenant_id, name, role, base_trust})

// --- belief state (semantic memory) ---
(:Claim {
    id,
    text,             // canonical natural-language claim
    fingerprint,      // normalised dedupe key, deterministic repeat detection
    status,           // 'active' | 'suspect' | 'quarantined' | 'corroborated' | 'retracted'
    verification,     // 'unsourced' | 'sourced' | 'contradicted'
    confidence,       // 0.0-1.0, as asserted
    created_at
})
(:Source {id, name, url, trust_level})   // trust_level: 'primary' | 'reputable' | 'unknown' | 'unreliable'

// --- immune response (procedural memory) ---
(:Infection {
    id,
    type,          // 'circular_support' | 'unsupported_root' | 'contradiction' | 'repeat_offender'
    severity,      // computed from graph reach, NOT from an LLM
    detected_at,
    resolved_at,
    status         // 'active' | 'contained' | 'resolved'
})
(:Antibody {
    id,
    correction_text,
    strategy,      // 'source_citation' | 'contradiction_surface' | 'quarantine_only'
    outcome,       // 'effective' | 'ignored' | 'unknown'  -> the learning signal
    created_at
})

// --- kernel of truth ---------------------------------------------------
//
// The swarm's memory of what is RIGHT, complementing Infection/quarantined
// Claim which record what was WRONG. Written by antibodies, read by the
// sanitiser. Only established when a source is cited: an unsourced correction
// ("nothing supports this") is a meta-statement, not a truth.
(:KernelFact {
    id,
    text,          // canonical, claim-shaped, kept short so lexical matching works
    trust_level,   // inherited from the source that backs it
    version,
    status,        // 'active' | 'superseded'
    created_at,
    superseded_by  // id of the fact that replaced this one, if any
})
```

### Why `fingerprint` exists

The original design detected repeats by asking an LLM to emit a `REPEATS` edge. That is
non-deterministic, and the "immunity recall" moment is the single most important beat in
the demo, it must never fail on stage.

`fingerprint` is a normalised key computed in Python (lowercase, strip punctuation and
stopwords, collapse whitespace). Exact repeats are then found with an indexed equality
lookup, not a guess. Semantic paraphrases ("Mars has oxygen at night") are caught by the
vector-recall path in §5, query 7.

---

## 3. Relationships

```cypher
// who said what (episodic -> semantic bridge)
(:Agent)-[:SPOKE {ts}]->(:Message)
(:Message)-[:MENTIONS]->(:Claim)
(:Agent)-[:ASSERTS {ts, message_id}]->(:Claim)
(:Session)-[:CONTAINS]->(:Message)
(:Session)-[:CONTAINS]->(:Claim)

// epistemic support structure, the substrate for every detection query
(:Claim)-[:SUPPORTED_BY {quote}]->(:Source)
(:Claim)-[:DERIVED_FROM {ts}]->(:Claim)      // child -> parent ("I inferred this from that")
(:Claim)-[:CONTRADICTS {detected_at, reason}]->(:Claim)
(:Claim)-[:REPEATS {similarity}]->(:Claim)

// social trust
(:Agent)-[:TRUSTS {weight}]->(:Agent)
(:Agent)-[:HANDOFF_TO {ts, context}]->(:Agent)

// immune response
(:Infection)-[:TARGETS]->(:Claim)            // root-cause claim
(:Infection)-[:SPREAD_TO]->(:Claim)          // materialised cascade members
(:Antibody)-[:NEUTRALIZES]->(:Infection)
(:Antibody)-[:INJECTS]->(:Claim)             // the corrective claim itself

// kernel of truth, provenance is edges, which is why the kernel is a graph
(:KernelFact)-[:DERIVED_FROM]->(:Source)     // what backs this fact
(:Antibody)-[:ESTABLISHES]->(:KernelFact)    // who taught the swarm this
(:KernelFact)-[:SUPERSEDES]->(:KernelFact)   // a newer fact retires an older one
(:Claim)-[:CONFLICTS_WITH {score}]->(:KernelFact)
(:Claim)-[:ALIGNED_WITH {score}]->(:KernelFact)
```

`DERIVED_FROM` direction matters and is easy to get backwards: the edge runs
**child → parent**. So a claim derived from the falsehood points *at* the falsehood,
and the cascade is walked as `(downstream)-[:DERIVED_FROM*1..5]->(root)`.

---

## 4. Indexes

```cypher
CREATE INDEX FOR (c:Claim)   ON (c.fingerprint)
CREATE INDEX FOR (c:Claim)   ON (c.status)
CREATE INDEX FOR (c:Claim)   ON (c.id)
CREATE INDEX FOR (a:Agent)   ON (a.name)
CREATE INDEX FOR (i:Infection) ON (i.status)
CREATE INDEX FOR (s:Session) ON (s.id)
CREATE INDEX FOR (k:KernelFact) ON (k.id)
CREATE INDEX FOR (k:KernelFact) ON (k.status)
```

`fingerprint` and `status` are the hot paths, immunity recall and the active-claim scan.
FalkorDB will not use an index for `<>` filters, so queries are written with positive
equality predicates wherever an index should be used.

---

## 5. The load-bearing queries

These are the product. Each is exposed as a tool the antibody agent can call and as a
result table in the UI.

### Q1, Circular support (epistemic cycle)

A supports B supports C supports A: the swarm's belief is self-justifying.

```cypher
MATCH path = (c:Claim)-[:SUPPORTED_BY|DERIVED_FROM*2..5]->(c)
RETURN c.id AS claim_id,
       c.text AS claim,
       [n IN nodes(path) | n.text] AS cycle_path,
       length(path) AS cycle_length
```

Fallback if relationship alternation inside a variable-length pattern is rejected by the
target FalkorDB build, run the two single-type variants and union them:

```cypher
MATCH path = (c:Claim)-[:DERIVED_FROM*2..5]->(c)
RETURN c.id, c.text, [n IN nodes(path) | n.text] AS cycle_path
```

> **Status: both forms verified working** on FalkorDB with the `graph` module (Oct 2026).
> Alternation inside a variable-length pattern is accepted, and a 3-hop cycle
> `A→B→C→A` is detected from each of its three starting nodes.

### Q2, Unsupported claims (no source, no derivation)

Written with `OPTIONAL MATCH` rather than a negated pattern predicate, because pattern
predicates in `WHERE` are not guaranteed across FalkorDB builds.

```cypher
MATCH (c:Claim {status: 'active'})
OPTIONAL MATCH (c)-[:SUPPORTED_BY]->(s:Source)
  WHERE s.trust_level <> 'unreliable'
WITH c, count(s) AS source_count
OPTIONAL MATCH (c)-[:DERIVED_FROM]->(p:Claim)
WITH c, source_count, count(p) AS parent_count
WHERE source_count = 0 AND parent_count = 0
RETURN c.id AS claim_id, c.text AS claim, c.fingerprint, c.created_at
ORDER BY c.created_at
```

### Q3, Contradiction clusters

Two agents with healthy trust holding incompatible beliefs.

```cypher
MATCH (c1:Claim)-[:CONTRADICTS]->(c2:Claim)
MATCH (a1:Agent)-[:ASSERTS]->(c1)
MATCH (a2:Agent)-[:ASSERTS]->(c2)
RETURN c1.id AS claim_a, c1.text AS text_a, a1.name AS agent_a,
       c2.id AS claim_b, c2.text AS text_b, a2.name AS agent_b,
       c2.created_at AS since
```

### Q4, Cascade reach (severity is computed from the graph)

This is the query that makes "FalkorDB is load-bearing" literal: *how bad the infection
is* is a property of graph reachability, not of anything an LLM said.

```cypher
MATCH (root:Claim {id: $root_claim_id})
OPTIONAL MATCH (down:Claim)-[:DERIVED_FROM*1..5]->(root)
WITH root, collect(DISTINCT down) AS downstream
UNWIND (CASE WHEN size(downstream) = 0 THEN [null] ELSE downstream END) AS d
OPTIONAL MATCH (a:Agent)-[:ASSERTS]->(d)
RETURN root.id AS root_id,
       root.text AS root_text,
       size(downstream) AS infected_claims,
       count(DISTINCT a) AS exposed_agents
```

`severity` is then a pure function of `infected_claims` and `exposed_agents`
(e.g. `1 + infected_claims + exposed_agents`), stored on the `Infection` node so the
dashboard can sort outbreaks without recomputation.

### Q5, Trust decay, derived rather than stored

The original design decremented a trust score in Python. Deriving it in Cypher keeps the
graph as the single source of truth: trust is *recomputed* from association, so it cannot
drift out of sync with the belief graph.

```cypher
MATCH (a:Agent)
OPTIONAL MATCH (a)-[:ASSERTS]->(:Claim)<-[:TARGETS]-(i:Infection)
  WHERE i.status IN ['active', 'contained']
WITH a, count(DISTINCT i) AS active_infections
RETURN a.name AS agent,
       a.base_trust AS base_trust,
       active_infections,
       a.base_trust - (0.15 * active_infections) AS effective_trust
ORDER BY effective_trust ASC
```

### Q6, Immunity recall (exact repeat)

The payoff beat: a previously-infected claim reappears and is flagged instantly, with no
re-detection of the original cascade.

```cypher
MATCH (past:Claim {fingerprint: $fingerprint})<-[:TARGETS]-(i:Infection)
RETURN past.id AS past_claim_id,
       past.text AS past_text,
       past.status AS past_status,
       i.id AS infection_id,
       i.type AS infection_type,
       i.detected_at AS first_seen,
       i.resolved_at AS contained_at
ORDER BY i.detected_at DESC
```

### Q7, Semantic immunity recall (paraphrase)

Catches "Mars has oxygen at night" against a quarantined "Mars has breathable oxygen at
night". Requires a vector index over `Claim.embedding`.

**Verified against a live instance** (FalkorDB with the `graph` module, Oct 2026).

```cypher
CREATE VECTOR INDEX FOR (c:Claim) ON (c.embedding)
OPTIONS {dimension: 1536, similarityFunction: 'cosine'}
```

```cypher
CALL db.idx.vector.queryNodes('Claim', 'embedding', $k, vecf32($query_embedding))
YIELD node AS candidate, score
MATCH (candidate)<-[:TARGETS]-(i:Infection)
RETURN candidate.id AS claim_id,
       candidate.text AS claim,
       candidate.status,
       score AS cosine_distance,
       i.type AS infection_type
ORDER BY score ASC
```

Two behaviours were confirmed by execution, not assumed:

1. The query vector **must** be wrapped in `vecf32(...)`. Passing a bare list, or a stored
   vector property directly, raises
   `Invalid arguments for procedure 'db.idx.vector.queryNodes'`.
2. The yielded `score` is a cosine **distance** (0.0 = identical) even though the index
   option is named `similarityFunction`. `ORDER BY score DESC` therefore returns the *least*
   similar candidates, a silent correctness bug that only shows up once more than `k`
   claims exist. Order ascending.

**Fallback if vector support is unavailable:** `Q7B_RECALL_CANDIDATES` pulls the candidate
set from the graph (claims carrying an infection) and scores token-overlap similarity in
Python via `models.similarity()`. The graph still supplies the recall set, so FalkorDB
remains load-bearing; only the scoring differs.

### Q8, Antibody effectiveness (does immunity actually improve?)

Turns "the swarm develops immunity" from a claim into a measurement.

```cypher
MATCH (ab:Antibody)-[:NEUTRALIZES]->(i:Infection)
RETURN i.type AS infection_type,
       count(i) AS outbreaks,
       sum(CASE WHEN ab.outcome = 'effective' THEN 1 ELSE 0 END) AS contained,
       sum(CASE WHEN ab.outcome = 'ignored' THEN 1 ELSE 0 END) AS recurred
ORDER BY outbreaks DESC
```

---

## 5d. Negative memory, and the self-audit that fills it

`RefutedStatement` is the third memory beside `Infection` (what was wrong) and `KernelFact` (what is
true). It holds statements confirmed false that **no fact contradicts**, because nothing they assert
is denied. "The Starter plan costs 499 USD per seat per year" is refuted by no Halcyon fact; what is
missing is the knowledge that no Starter plan exists.

The sanitiser consults them before any comparison, subject-bound, and cheapest first.

They are produced by `src/immunegraph/audit.py`, which mutates the kernel's own facts, scores the
defence on both sides, and writes the uncovered ones back:

| Measure | Meaning |
| --- | --- |
| precision / recall / F1 | standard, on a generated labelled set |
| autoimmunity | true statements wrongly flagged |
| attribution | caught against the right fact rather than by luck |

Both errors found by running it are pinned in `tests/test_audit.py`: subject-blind conflict matching
(20% autoimmunity) and the naive vaccination that replaced it (82%).

## 5c. External contradictions, grounding a visitor's claim

The Mars scenario is scripted; **Check any claim** is not. A visitor submits a statement, it is
grounded against Wikipedia (`src/immunegraph/grounding.py`), and a refutation is treated by the
same machinery that handles everything else the swarm catches.

`Infection.type` therefore takes a third value:

| type | how it is discovered |
| --- | --- |
| `unsupported_root` | **Q2**, active, no trusted source, no parent |
| `repeat_offender` | **Q6/Q7**, matches something already neutralised |
| `external_contradiction` | **grounding**, a public source refutes it |

The shape written on a refutation:

```
(Claim)<-[:TARGETS]-(Infection {type: 'external_contradiction'})
(Infection)-[:NEUTRALIZES]-(Antibody {strategy: 'external_grounding'})
(Antibody)-[:INJECTS]->(Claim {verification: 'sourced'})
(correction)-[:SUPPORTED_BY {quote}]->(Source {name: 'Wikipedia: ...', trust_level: 'reputable'})
(correction)-[:CONTRADICTS]->(Claim {status: 'quarantined'})
```

Storing the deciding sentence as the `SUPPORTED_BY` quote means **why** a claim was refuted is a
traversal rather than a log line, the same principle as every other verdict here.

Because a refutation also registers a `KernelFact`, a second visitor repeating that claim is
caught by the sanitiser with **no network call at all**.

## 5a. Infection types

Five, from `src/immunegraph/schema.py`:

| type | discovered by | meaning |
| --- | --- | --- |
| `unsupported_root` | **Q2** | active claim, no trusted source, no parent |
| `circular_support` | **Q1** | the support structure contains a cycle |
| `contradiction` | **Q3** | two asserted claims disagree, within or across agents |
| `repeat_offender` | **Q6 / Q7** | matches something already neutralised |
| `external_contradiction` | grounding | a public source refutes it |

## 5b. The kernel of truth, recognising lies the swarm already knows

Q1-Q8 **detect new lies** by walking the support structure. They are thorough and they are
expensive: extraction runs, then a scan. The kernel is the other half of immunity, a set of
canonical, source-backed facts that new claims are judged against by comparison.

| | Detection (Q1-Q8) | Sanitisation (K1-K5) |
| --- | --- | --- |
| Finds | lies the swarm has **never seen** | lies it **already knows** |
| Method | traversal over the support structure | lexical match against facts |
| Cost | extraction + full scan | one fact query, then comparison per fact |
| Measured | 20.3 ms sweep on a 12-node graph, local | 1.31 ms median, 2.25 ms p95, no model call |

An antibody that cites a source establishes a fact. An antibody that cannot cite one
establishes **nothing**, "no source supports this" is a meta-statement, not a truth worth
remembering, and inventing certainty would just be a new infection.

### K1, the active kernel, with provenance

```cypher
MATCH (k:KernelFact)
WHERE k.status = 'active'
OPTIONAL MATCH (k)-[:DERIVED_FROM]->(s:Source)
RETURN k.id AS fact_id, k.text AS text, k.trust_level AS trust_level,
       k.version AS version, k.created_at AS created_at, s.name AS source
ORDER BY k.created_at
```

### K2, what currently conflicts with the kernel

```cypher
MATCH (c:Claim)-[r:CONFLICTS_WITH]->(k:KernelFact)
RETURN c.id AS claim_id, c.text AS claim, c.status AS claim_status,
       k.id AS fact_id, k.text AS fact, k.trust_level AS trust_level,
       r.score AS overlap
ORDER BY r.score DESC
```

### K3, kernel health (the number that shows immunity accumulating)

```cypher
MATCH (k:KernelFact)
WHERE k.status = 'active'
WITH count(k) AS facts
OPTIONAL MATCH (:Claim)-[al:ALIGNED_WITH]->(:KernelFact)
WITH facts, count(al) AS aligned_edges
OPTIONAL MATCH (:Claim)-[cf:CONFLICTS_WITH]->(:KernelFact)
RETURN facts, aligned_edges, count(cf) AS conflict_edges
```

> **`count(al)`, not `count(*)`.** Under `OPTIONAL MATCH` an unmatched pattern still yields
> one row of nulls, so `count(*)` reports **1** for an empty edge set. The first version of
> this query did exactly that and the dashboard cheerfully reported a conflict before any
> fact existed. Named-relationship counts ignore nulls; `count(*)` does not.

### K4, claims the sanitiser has not yet judged

```cypher
MATCH (c:Claim)
WHERE c.status = 'active' AND c.verdict IS NULL
RETURN c.id AS claim_id, c.text AS text, c.created_at AS created_at
ORDER BY c.created_at
LIMIT $limit
```

### K5, who established a fact, citing what

```cypher
MATCH (k:KernelFact {id: $fact_id})
OPTIONAL MATCH (ab:Antibody)-[:ESTABLISHES]->(k)
OPTIONAL MATCH (k)-[:DERIVED_FROM]->(s:Source)
OPTIONAL MATCH (i:Infection)-[:NEUTRALIZES|TARGETS*1..2]-(ab)
RETURN k.id AS fact_id, k.text AS text, k.trust_level AS trust_level,
       k.created_at AS created_at,
       ab.id AS established_by, s.name AS source, s.url AS source_url,
       collect(DISTINCT i.id) AS related_infections
```

### Matching a short claim against a longer fact

Kernel facts are canonical and terse, but the LLM path has no hand-written short form, so its
fact may be prose-length. **Jaccard divides by the union**, which penalises a length
difference and drops a real contradiction below threshold. Kernel comparisons therefore use
the **overlap coefficient**, `|A ∩ B| / min(|A|, |B|)`, with a higher threshold (`0.60`) and
the same negation-asymmetry rule as `detect_contradiction`.

This is worth being precise about, because the honest version is narrower than it first
looks: against the Mars correction's short canonical fact, Jaccard already sufficed. The
overlap coefficient is what saves the **prose-length** fact, which is exactly what the LLM
path produces. `tests/test_models.py` records that distinction rather than overclaiming.

---

## 6. FalkorDB dialect constraints that shaped this model

Verified against the official Cypher compatibility and limitations pages.

1. **No regex operator.** Similarity and normalisation happen in Python, never in Cypher.
   Use `CONTAINS` / `STARTS WITH` / `ENDS WITH` if string matching is required.
2. **No label expressions** in `MATCH` (e.g. `(n:A|B)`). Match each label separately and
   combine with `UNION`.
3. **No aggregation inside pattern comprehensions.** Aggregate in a preceding `WITH`.
4. **Unnamed relationships in a pattern only verify existence, not multiplicity.**
   If a count must be accurate, explicitly reference the relationship alias.
5. **`LIMIT` does not constrain eager operations** (`CREATE`, `SET`, `DELETE`, `MERGE`).
   Never write `CREATE ... RETURN x LIMIT 5` expecting 5 nodes.
6. **No index support for `<>` filters**, write positive equality predicates.
7. `LIMIT`/`SKIP`/`ORDER BY` and `UNWIND`, `MERGE`, `OPTIONAL MATCH`, `WITH`, `UNION`
   are all supported, as are list comprehensions and `size()`.
8. `CALL` supports **procedures only**, no user-defined functions (Flex UDF library aside).

Every query above must be exercised by `python -m immunegraph.verify_queries` against a
live instance and pass before it is considered done. A query that has never been executed
is not a feature, it is a hope.
