# ImmuneGraph — Graph Data Model

> This document is the authoritative schema. Every Cypher query in the product is
> written against this model. If you change the model, update this file in the same commit.

FalkorDB is not a storage layer here. The detection algorithms **are** Cypher queries,
and the immune response is a graph write. Remove FalkorDB and there is no product.

Dialect note: FalkorDB implements a **subset of openCypher v9**. Known constraints that
shaped this model are listed at the bottom — read them before writing new queries.

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
Multi-tenancy is handled by FalkorDB's multi-graph feature — one graph per tenant
(`select_graph(f"tenant_{tenant_id}")`) — so isolation is physical as well as logical.

---

## 2. Nodes

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
    fingerprint,      // normalised dedupe key — deterministic repeat detection
    status,           // 'active' | 'quarantined' | 'corroborated' | 'retracted'
    verification,     // 'unsourced' | 'sourced' | 'contradicted'
    confidence,       // 0.0–1.0, as asserted
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
```

### Why `fingerprint` exists

The original design detected repeats by asking an LLM to emit a `REPEATS` edge. That is
non-deterministic, and the "immunity recall" moment is the single most important beat in
the demo — it must never fail on stage.

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

// epistemic support structure — the substrate for every detection query
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
```

`fingerprint` and `status` are the hot paths — immunity recall and the active-claim scan.
FalkorDB will not use an index for `<>` filters, so queries are written with positive
equality predicates wherever an index should be used.

---

## 5. The load-bearing queries

These are the product. Each is exposed as a tool the antibody agent can call and as a
result table in the UI.

### Q1 — Circular support (epistemic cycle)

A supports B supports C supports A: the swarm's belief is self-justifying.

```cypher
MATCH path = (c:Claim)-[:SUPPORTED_BY|DERIVED_FROM*2..5]->(c)
RETURN c.id AS claim_id,
       c.text AS claim,
       [n IN nodes(path) | n.text] AS cycle_path,
       length(path) AS cycle_length
```

Fallback if relationship alternation inside a variable-length pattern is rejected by the
target FalkorDB build — run the two single-type variants and union them:

```cypher
MATCH path = (c:Claim)-[:DERIVED_FROM*2..5]->(c)
RETURN c.id, c.text, [n IN nodes(path) | n.text] AS cycle_path
```

### Q2 — Unsupported claims (no source, no derivation)

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

### Q3 — Contradiction clusters

Two agents with healthy trust holding incompatible beliefs.

```cypher
MATCH (c1:Claim)-[:CONTRADICTS]->(c2:Claim)
MATCH (a1:Agent)-[:ASSERTS]->(c1)
MATCH (a2:Agent)-[:ASSERTS]->(c2)
RETURN c1.id AS claim_a, c1.text AS text_a, a1.name AS agent_a,
       c2.id AS claim_b, c2.text AS text_b, a2.name AS agent_b,
       c2.created_at AS since
```

### Q4 — Cascade reach (severity is computed from the graph)

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

### Q5 — Trust decay, derived rather than stored

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

### Q6 — Immunity recall (exact repeat)

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

### Q7 — Semantic immunity recall (paraphrase)

Catches "Mars has oxygen at night" against a quarantined "Mars has breathable oxygen at
night". Requires a vector index over `Claim.embedding`.

```cypher
CREATE VECTOR INDEX FOR (c:Claim) ON (c.embedding)
OPTIONS {dimension: 1536, similarityFunction: 'cosine'}
```

```cypher
MATCH (q:Claim {id: $query_claim_id})
CALL db.idx.vector.queryNodes('Claim', 'embedding', 5, q.embedding)
YIELD node AS candidate, score
MATCH (candidate)<-[:TARGETS]-(i:Infection)
RETURN candidate.id AS claim_id,
       candidate.text AS claim,
       candidate.status,
       score,
       i.type AS infection_type
ORDER BY score DESC
```

> **Verify the exact vector-index syntax against the running instance before relying on
> it.** FalkorDB has changed this surface between releases. If it does not work, fall back
> to the extractor emitting `REPEATS` edges plus a Python-side cosine comparison over
> embeddings stored as `Claim.embedding` lists.

### Q8 — Antibody effectiveness (does immunity actually improve?)

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
6. **No index support for `<>` filters** — write positive equality predicates.
7. `LIMIT`/`SKIP`/`ORDER BY` and `UNWIND`, `MERGE`, `OPTIONAL MATCH`, `WITH`, `UNION`
   are all supported, as are list comprehensions and `size()`.
8. `CALL` supports **procedures only** — no user-defined functions (Flex UDF library aside).

Every query above must be exercised by `python -m immunegraph.verify_queries` against a
live instance and pass before it is considered done. A query that has never been executed
is not a feature, it is a hope.
